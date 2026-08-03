' Trawl launcher shim.
' A shortcut pointing at powershell.exe shows a console for the life of the script, and
' -WindowStyle Hidden on the shortcut still flashes one first. Window style 0 here shows
' nothing at all. -Silent makes launch.ps1 report errors through a dialog instead of a
' console that no longer exists.

Dim shell, fso, here
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)

shell.CurrentDirectory = here
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & here & "\launch.ps1"" -Silent", 0, False
