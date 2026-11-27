# Install PySME-GUI for the current user on Windows (64-bit x86).
# No administrator rights are needed. Start it with "Install PySME.cmd", or:
#
#   install.ps1              install, or reinstall from this folder
#   install.ps1 -Repair      also rebuild PySME from its package list
#   install.ps1 -Uninstall
#
# Python and PySME go into %LOCALAPPDATA%\PySME-GUI, and a "PySME" shortcut
# onto the desktop and into the Start menu.
#
# This file is kept in plain ASCII: Windows PowerShell 5.1 would misread others.
param([switch]$Repair, [switch]$Uninstall)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"    # its progress bar slows downloads greatly

$Root = Split-Path -Parent $PSScriptRoot
$Data = if ($env:PYSME_GUI_HOME) { $env:PYSME_GUI_HOME } else { Join-Path $env:LOCALAPPDATA "PySME-GUI" }
$Links = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "PySME.lnk"),
    (Join-Path ([Environment]::GetFolderPath("DesktopDirectory")) "PySME.lnk")
)
$UvVersion = "0.13.10"

# WScript.Shell, the usual way to make a shortcut, refuses paths with characters
# outside the system's code page, such as some user names. This calls the Unicode
# interface of Windows itself.
$ShortcutSource = @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

namespace PySMEInstaller
{
    // The methods must be listed in Windows' own order; the unused ones hold
    // their places.
    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellLinkW
    {
        void GetPath(IntPtr file, int length, IntPtr findData, uint flags);
        void GetIDList(out IntPtr list);
        void SetIDList(IntPtr list);
        void GetDescription(IntPtr name, int length);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetWorkingDirectory(IntPtr directory, int length);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string directory);
        void GetArguments(IntPtr arguments, int length);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string arguments);
        void GetHotkey(out short hotkey);
        void SetHotkey(short hotkey);
        void GetShowCmd(out int command);
        void SetShowCmd(int command);
        void GetIconLocation(IntPtr path, int length, out int index);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string path, int index);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, uint reserved);
        void Resolve(IntPtr window, uint flags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string file);
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    class ShellLink { }

    public static class Shortcut
    {
        public static void Create(string link, string target, string arguments,
                                  string directory, string icon, string description)
        {
            IShellLinkW shortcut = (IShellLinkW)new ShellLink();
            try
            {
                shortcut.SetPath(target);
                shortcut.SetArguments(arguments);
                shortcut.SetWorkingDirectory(directory);
                shortcut.SetIconLocation(icon, 0);
                shortcut.SetDescription(description);
                ((IPersistFile)shortcut).Save(link, true);
            }
            finally
            {
                Marshal.ReleaseComObject(shortcut);
            }
        }
    }
}
'@

function Remove-Installation {
    if (Test-Path -LiteralPath $Data) {
        # Never delete a folder that is not an installation of this program.
        $ours = ((Split-Path -Leaf $Data) -eq "PySME-GUI") -or
                (Test-Path -LiteralPath (Join-Path $Data "current.json"))
        if (-not $ours) { throw "$Data does not look like a PySME-GUI folder; nothing was removed." }
        # Checked before anything is deleted, so that a running PySME is not left broken.
        $inside = $Data.TrimEnd("\") + "\"
        $running = Get-Process | Where-Object {
            $_.Path -and $_.Path.StartsWith($inside, [StringComparison]::OrdinalIgnoreCase)
        }
        if ($running) { throw "PySME is running. Quit it first (in its window: ... > Quit PySME), then try again." }
        try {
            Remove-Item -LiteralPath $Data -Recurse -Force
        } catch {
            # Some file names are too long for PowerShell; this form of rd takes them.
            cmd.exe /d /c rd /s /q "\\?\$Data"
        }
        if (Test-Path -LiteralPath $Data) { throw "Some files in $Data could not be removed." }
    }
    foreach ($link in $Links) {
        if (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link -Force }
    }
}

function Get-Uv {
    $uv = Join-Path $Data "bin\uv.exe"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $uv) | Out-Null
    if ($env:PYSME_GUI_UV) {
        if ($env:PYSME_GUI_UV -ne $uv) { Copy-Item -LiteralPath $env:PYSME_GUI_UV -Destination $uv -Force }
        return $uv
    }
    if (Test-Path -LiteralPath $uv) { return $uv }
    $name = "uv-x86_64-pc-windows-msvc.zip"
    $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$name"
    $work = Join-Path ([IO.Path]::GetTempPath()) ("pysme-uv-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        Write-Host "Downloading uv $UvVersion"
        [Net.ServicePointManager]::SecurityProtocol =
            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $zip = Join-Path $work $name
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
        Invoke-WebRequest -UseBasicParsing -Uri "$url.sha256" -OutFile "$zip.sha256"
        $expected = ((Get-Content -LiteralPath "$zip.sha256" -Raw).Trim() -split "\s+")[0]
        $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
        if ($actual -ne $expected) { throw "The downloaded installer tool is damaged (checksum differs). Try again." }
        Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $work "uv")
        $found = Get-ChildItem -LiteralPath (Join-Path $work "uv") -Recurse -Filter "uv.exe" | Select-Object -First 1
        Copy-Item -LiteralPath $found.FullName -Destination $uv -Force
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $uv
}

function Install-PySME {
    $machine = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    if ($machine -ne "AMD64") { throw "PySME needs 64-bit x86 Windows; this computer is $machine." }
    if (-not (Test-Path -LiteralPath (Join-Path $Root "pysme_gui\envmanager.py"))) {
        throw "The program files are missing. Extract the whole zip file first, then run the installer from the extracted folder."
    }

    New-Item -ItemType Directory -Force -Path $Data | Out-Null
    $uv = Get-Uv

    foreach ($name in "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX") {
        Remove-Item -LiteralPath "Env:\$name" -ErrorAction SilentlyContinue
    }
    $env:PYSME_GUI_HOME = $Data
    $env:PYSME_GUI_UV = $uv
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Data "python"
    $env:UV_CACHE_DIR = Join-Path $Data "cache"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONDONTWRITEBYTECODE = "1"

    $arguments = @(
        "run", "--no-project", "--managed-python", "--python", "3.12",
        (Join-Path $Root "pysme_gui\envmanager.py"), "install", "--source", $Root
    )
    if ($Repair) { $arguments += "--repair" }
    & $uv @arguments
    if ($LASTEXITCODE -ne 0) { throw "The installation did not finish. The lines above say why." }

    # uv prints UTF-8; read it as such, or a user name in another script is garbled.
    $encoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        $python = (& $uv python find --no-project --managed-python 3.12 | Select-Object -Last 1)
    } finally {
        [Console]::OutputEncoding = $encoding
    }
    if (-not $python) { throw "The installed Python was not found." }
    $pythonw = Join-Path (Split-Path -Parent $python.Trim()) "pythonw.exe"
    if (-not (Test-Path -LiteralPath $pythonw)) { throw "pythonw.exe was not found next to $python" }

    # Copies of these stay if the downloaded folder is removed.
    $launcher = Join-Path $Data "launch.py"
    $icon = Join-Path $Data "pysme.ico"
    Copy-Item -LiteralPath (Join-Path $Root "windows\launch.py") -Destination $launcher -Force
    Copy-Item -LiteralPath (Join-Path $Root "assets\pysme-icon.ico") -Destination $icon -Force

    Add-Type -TypeDefinition $ShortcutSource
    foreach ($link in $Links) {
        [PySMEInstaller.Shortcut]::Create(
            $link, $pythonw, ('"' + $launcher + '"'), $env:USERPROFILE, $icon,
            "Spectroscopy Made Easy")
    }

    Write-Host ""
    Write-Host "Done. Start PySME from the desktop or the Start menu."
    Write-Host "It opens in your web browser. To quit: ... > Quit PySME. Closing the tab"
    Write-Host "leaves it running, and starting PySME again shows it as you left it."
}

try {
    if ($Uninstall) {
        Remove-Installation
        Write-Host "Removed PySME-GUI. Model data in the .sme folder of your user folder was kept."
    } else {
        Install-PySME
    }
} catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
exit 0
