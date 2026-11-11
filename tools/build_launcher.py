from pathlib import Path
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parents[1]
assets = root/'assets'
assets.mkdir(exist_ok=True)
icon = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(icon)
draw.rounded_rectangle((4, 4, 252, 252), radius=58, fill='#183b36')
font = ImageFont.truetype(str(Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'/'segoeuib.ttf'), 180)
draw.text((128, 119), 'C', anchor='mm', font=font, fill='#7cf0d7')
icon.save(assets/'Clearframe.ico', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
compiler = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Microsoft.NET'/'Framework64'/'v4.0.30319'/'csc.exe'
subprocess.run([str(compiler), '/nologo', '/target:winexe', '/out:'+str(root/'Clearframe.exe'),
                '/win32icon:'+str(assets/'Clearframe.ico'), '/reference:System.Windows.Forms.dll',
                str(root/'tools'/'launcher.cs')], check=True, cwd=str(root))
print('Built Clearframe.exe')
