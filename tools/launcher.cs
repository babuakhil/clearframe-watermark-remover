using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class ClearframeLauncher {
    [STAThread]
    static void Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
        string script = Path.Combine(root, "run.py");
        if (!File.Exists(python) || !File.Exists(script)) {
            MessageBox.Show("Keep Clearframe.exe in its application folder. If the runtime is missing, run Setup.ps1 first.",
                            "Clearframe", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        var start = new ProcessStartInfo(python, "\"" + script + "\"");
        start.WorkingDirectory = root;
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        foreach (string arg in args) {
            start.Arguments += " \"" + arg.Replace("\"", "") + "\"";
        }
        try { Process.Start(start); }
        catch (Exception ex) { MessageBox.Show(ex.Message, "Clearframe could not start"); }
    }
}
