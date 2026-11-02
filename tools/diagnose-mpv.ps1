$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$root = "C:\Users\Dominiczka\Downloads\YT-Cut"
$dll = Join-Path $root "_internal\tools\libmpv-2.dll"
$log = Join-Path $env:TEMP "YT-Cut-mpv-debug.log"
$source = Get-ChildItem $env:TEMP -Directory -Filter "yt-cut-*" -ErrorAction SilentlyContinue |
    ForEach-Object { Get-Item (Join-Path $_.FullName "source.mkv") -ErrorAction SilentlyContinue } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (!(Test-Path $dll)) { throw "Missing DLL: $dll" }
if (-not [Environment]::Is64BitProcess) { throw "Requires 64-bit PowerShell to load x64 libmpv" }
$env:PATH = (Split-Path $dll) + ";" + (Join-Path $root "_internal") + ";" + $env:PATH
if (!$source) { throw "No source.mkv found. Download the video again without closing YT Cut." }

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class MpvDiagnostic {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
    public static extern bool SetDllDirectory(string path);
    [DllImport("kernel32.dll", EntryPoint="LoadLibraryExW", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr LoadLibraryEx(string filename, IntPtr reserved, uint flags);
    [DllImport("libmpv-2.dll", CallingConvention=CallingConvention.Cdecl)]
    public static extern IntPtr mpv_create();
    [DllImport("libmpv-2.dll", CallingConvention=CallingConvention.Cdecl, CharSet=CharSet.Ansi)]
    public static extern int mpv_set_option_string(IntPtr h, string name, string value);
    [DllImport("libmpv-2.dll", CallingConvention=CallingConvention.Cdecl)]
    public static extern int mpv_initialize(IntPtr h);
    [DllImport("libmpv-2.dll", CallingConvention=CallingConvention.Cdecl)]
    private static extern int mpv_command(IntPtr h, IntPtr argv);
    [DllImport("libmpv-2.dll", CallingConvention=CallingConvention.Cdecl, CharSet=CharSet.Ansi)]
    private static extern int mpv_get_property(IntPtr h, string name, int fmt, out double v);
    [DllImport("libmpv-2.dll", EntryPoint="mpv_get_property", CallingConvention=CallingConvention.Cdecl, CharSet=CharSet.Ansi)]
    private static extern int get_flag(IntPtr h, string name, int fmt, out int v);
    [DllImport("libmpv-2.dll", CallingConvention=CallingConvention.Cdecl)]
    private static extern IntPtr mpv_error_string(int error);
    [DllImport("libmpv-2.dll", CallingConvention=CallingConvention.Cdecl)]
    public static extern void mpv_terminate_destroy(IntPtr h);
    public static string Error(int v) { return v >= 0 ? "OK" : Marshal.PtrToStringAnsi(mpv_error_string(v)) + " [" + v + "]"; }
    public static string Double(IntPtr h, string key) {
        double v; int e=mpv_get_property(h,key,5,out v);
        return e>=0?v.ToString("F3",System.Globalization.CultureInfo.InvariantCulture):Error(e);
    }
    public static string Flag(IntPtr h, string key) {
        int v; int e=get_flag(h,key,3,out v);
        return e>=0?v.ToString():Error(e);
    }
    public static int Command(IntPtr h, string[] args) {
        IntPtr[] p=new IntPtr[args.Length+1]; IntPtr array=IntPtr.Zero;
        try {
            for(int i=0;i<args.Length;i++) {
                byte[] b=Encoding.UTF8.GetBytes(args[i]+"\0");
                p[i]=Marshal.AllocHGlobal(b.Length);
                Marshal.Copy(b,0,p[i],b.Length);
            }
            array=Marshal.AllocHGlobal(IntPtr.Size*p.Length);
            Marshal.Copy(p,0,array,p.Length);
            return mpv_command(h,array);
        } finally {
            if(array!=IntPtr.Zero) Marshal.FreeHGlobal(array);
            for(int i=0;i<args.Length;i++) if(p[i]!=IntPtr.Zero) Marshal.FreeHGlobal(p[i]);
        }
    }
}
'@

[void][MpvDiagnostic]::SetDllDirectory((Split-Path $dll))
$loaded = [MpvDiagnostic]::LoadLibraryEx($dll, [IntPtr]::Zero, 0x1100)
if ($loaded -eq [IntPtr]::Zero) {
    $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    throw "Windows loader cannot load libmpv with its dependencies: Win32 $code. Check missing dependent DLLs in _internal."
}
Write-Host "LOADLIBRARYEX: OK, handle=$loaded"
$form = New-Object System.Windows.Forms.Form
$form.Text = "YT Cut - libmpv diagnostics"
$form.Size = New-Object System.Drawing.Size(850,580)
$panel = New-Object System.Windows.Forms.Panel
$panel.Dock = "Fill"
$panel.BackColor = [System.Drawing.Color]::Black
$form.Controls.Add($panel)
$form.Show()
[System.Windows.Forms.Application]::DoEvents()
$hwnd = [uint32]($panel.Handle.ToInt64() -band 4294967295L)
Write-Host "SOURCE: $($source.FullName)"
Write-Host "DLL: $dll"
Write-Host "HWND: $hwnd"
Write-Host "LOG: $log"

$ctx = [MpvDiagnostic]::mpv_create()
if ($ctx -eq [IntPtr]::Zero) { throw "mpv_create failed" }
try {
    $options = [ordered]@{
        "config"="no"; "wid"="$hwnd"; "idle"="yes"; "force-window"="yes";
        "keep-open"="yes"; "pause"="yes"; "osc"="no";
        "input-default-bindings"="no"; "terminal"="no"; "hwdec"="auto-safe";
        "log-file"="$log"; "msg-level"="all=v"
    }
    foreach($item in $options.GetEnumerator()) {
        $e = [MpvDiagnostic]::mpv_set_option_string($ctx,$item.Key,$item.Value)
        if ($e -lt 0) { throw "Option $($item.Key): $([MpvDiagnostic]::Error($e))" }
    }
    $e = [MpvDiagnostic]::mpv_initialize($ctx)
    Write-Host "INITIALIZE: $([MpvDiagnostic]::Error($e))"
    if ($e -lt 0) { throw "mpv_initialize failed" }
    $e = [MpvDiagnostic]::Command($ctx,[string[]]@("loadfile",$source.FullName,"replace"))
    Write-Host "LOADFILE: $([MpvDiagnostic]::Error($e))"
    $e = [MpvDiagnostic]::Command($ctx,[string[]]@("set","pause","no"))
    Write-Host "PLAY: $([MpvDiagnostic]::Error($e))"
    for ($i=0; $i -lt 15; $i++) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 1000
        Write-Host ("{0,2}s pos={1} pause={2} idle={3} video={4}" -f
          ($i+1),[MpvDiagnostic]::Double($ctx,"time-pos"),
          [MpvDiagnostic]::Flag($ctx,"pause"),
          [MpvDiagnostic]::Flag($ctx,"core-idle"),
          [MpvDiagnostic]::Flag($ctx,"vo-configured"))
    }
} finally {
    [MpvDiagnostic]::mpv_terminate_destroy($ctx)
    $form.Close()
}
Write-Host "MPV LOG - last 65 lines:"
Get-Content $log -Tail 65
Write-Host "Full log: $log"
