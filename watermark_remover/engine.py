"""Bounded, source-scale ProPainter adapter. No blur/inpaint fallback."""
from __future__ import annotations

from pathlib import Path
import gc
import sys
import warnings

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


class Cancelled(Exception):
    pass


class ProPainterEngine:
    def __init__(self, precision='fp16', notify=lambda **kw: None, cancelled=lambda: False):
        import torch
        self.torch = torch
        self.notify = notify
        self.cancelled = cancelled
        if not torch.cuda.is_available():
            raise RuntimeError('An NVIDIA CUDA GPU is required for high-quality temporal repair. Check your NVIDIA driver.')
        vendor = ROOT / 'vendor' / 'ProPainter'
        if not (vendor / 'model' / 'propainter.py').exists():
            raise RuntimeError('ProPainter source is missing. Run Setup.ps1 in the application folder.')
        for name in ('raft-things.pth', 'recurrent_flow_completion.pth', 'ProPainter.pth'):
            if not (ROOT / 'models' / name).exists():
                raise RuntimeError('AI weights are missing. Run Setup.ps1 to download the official models.')
        sys.path.insert(0, str(vendor))
        from model.modules.flow_comp_raft import RAFT_bi
        from model.recurrent_flow_completion import RecurrentFlowCompleteNet
        from model.propainter import InpaintGenerator
        torch.set_num_threads(4)
        torch.manual_seed(42)
        torch.backends.cudnn.benchmark = False
        torch.backends.cuda.matmul.allow_tf32 = False
        self.device = torch.device('cuda')
        self.half = precision == 'fp16'
        self.notify(stage='Loading AI', detail=torch.cuda.get_device_name(0))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', FutureWarning)
            self.raft = RAFT_bi(str(ROOT / 'models' / 'raft-things.pth'), self.device).eval()
            self.flow = RecurrentFlowCompleteNet(str(ROOT / 'models' / 'recurrent_flow_completion.pth')).to(self.device).eval()
            self.model = InpaintGenerator(model_path=str(ROOT / 'models' / 'ProPainter.pth')).to(self.device).eval()
        if self.half:
            self.flow.half()
            self.model.half()
        for network in (self.raft, self.flow, self.model):
            for parameter in network.parameters():
                parameter.requires_grad_(False)

    def check_cancel(self):
        if self.cancelled():
            raise Cancelled()

    def repair(self, images: list[np.ndarray], masks: list[np.ndarray]) -> list[np.ndarray]:
        try:
            return self._repair(images, masks)
        except self.torch.cuda.OutOfMemoryError:
            # Leave the exception scope before retrying so failed tensors are freed.
            pass
        gc.collect()
        self.torch.cuda.empty_cache()
        if len(images) <= 8:
            raise RuntimeError('This native crop exceeds available GPU memory even with a short window. Close GPU-heavy applications or reduce Context. The source resolution has not been reduced.')
        self.check_cancel()
        middle = len(images)//2
        overlap = min(3, middle//2)
        self.notify(stage='Adapting to GPU memory', detail=f'Using shorter overlapping windows at the same {images[0].shape[1]} × {images[0].shape[0]} pixel scale')
        left = self.repair(images[:middle+overlap], masks[:middle+overlap])
        right = self.repair(images[middle-overlap:], masks[middle-overlap:])
        return left[:middle] + right[overlap:]

    def _repair(self, images: list[np.ndarray], masks: list[np.ndarray]) -> list[np.ndarray]:
        torch = self.torch
        self.check_cancel()
        count = len(images)
        if count == 1:
            result = self.repair(images * 2, masks * 2)
            return result[:1]
        height, width = images[0].shape[:2]
        # RAFT pyramid needs at least 128 pixels. Padding never changes image scale.
        pad_h = max(128, (height + 7) // 8 * 8)
        pad_w = max(128, (width + 7) // 8 * 8)
        array = np.stack([np.pad(im, ((0, pad_h-height), (0, pad_w-width), (0, 0)), mode='edge') for im in images])
        mask_array = np.stack([np.pad(m, ((0, pad_h-height), (0, pad_w-width)), mode='edge') for m in masks])
        with torch.inference_mode():
            frames = torch.from_numpy(array).permute(0, 3, 1, 2).float().unsqueeze(0).to(self.device) / 127.5 - 1
            mask = torch.from_numpy(mask_array).float().unsqueeze(0).unsqueeze(2).to(self.device)
            self.notify(stage='Analyzing motion', detail=f'{width} × {height} crop · {count} frames')
            forward, backward = [], []
            # Bound RAFT's all-pairs correlation independently of the temporal model.
            for start in range(0, count - 1, 2):
                self.check_cancel()
                flows = self.raft(frames[:, start:min(count, start+3)], iters=20)
                forward.append(flows[0])
                backward.append(flows[1])
            flows = (torch.cat(forward, dim=1), torch.cat(backward, dim=1))
            if self.half:
                frames, mask = frames.half(), mask.half()
                flows = (flows[0].half(), flows[1].half())
            self.check_cancel()
            self.notify(stage='Recovering background', detail='Propagating detail from neighboring frames')
            completed, _ = self.flow.forward_bidirect_flow(flows, mask)
            completed = self.flow.combine_flow(flows, completed, mask)
            propagated, remaining = self.model.img_propagation(frames * (1-mask), completed, mask, 'nearest')
            updated = frames * (1-mask) + propagated.view(1, count, 3, pad_h, pad_w) * mask
            remaining = remaining.view(1, count, 1, pad_h, pad_w)
            # Use the prediction nearest the center of its local window. No averaging
            # of neighboring frames or competing predictions that can soften edges.
            best_distance = [10**9] * count
            results = [None] * count
            stride = 5
            for center in range(0, count + stride, stride):
                self.check_cancel()
                local = list(range(max(0, center-5), min(count, center+6)))
                if len(local) < 2:
                    continue
                refs = [i for i in range(0, count, 8) if i not in local]
                ids = local + refs
                flow_local = (completed[0][:, local[:-1]], completed[1][:, local[:-1]])
                self.notify(stage='Refining detail', detail=f'Temporal pass {min(center, count)}/{count}')
                predicted = self.model(updated[:, ids], flow_local, mask[:, ids], remaining[:, ids], len(local))
                predicted = ((predicted.view(-1, 3, pad_h, pad_w).float() + 1) * 127.5).clamp_(0, 255)
                predicted = predicted.cpu().permute(0, 2, 3, 1).numpy().round().astype(np.uint8)
                for index, target in enumerate(local):
                    distance = abs(center-target)
                    if distance < best_distance[target]:
                        result = images[target].copy()
                        original_mask = masks[target].astype(bool)
                        result[original_mask] = predicted[index, :height, :width][original_mask]
                        results[target] = result
                        best_distance[target] = distance
                del predicted
            del frames, mask, flows, completed, propagated, remaining, updated
        gc.collect()
        torch.cuda.empty_cache()
        return results
