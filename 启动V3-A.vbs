Option Explicit

Dim shell, fso, root, batPath, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = fso.BuildPath(root, "START_V3A.bat")

If Not fso.FileExists(batPath) Then
  MsgBox "START_V3A.bat was not found.", vbCritical, "Cocos2D PAK Localization Studio"
  WScript.Quit 1
End If

shell.CurrentDirectory = root
command = "cmd.exe /c " & Chr(34) & Chr(34) & batPath & Chr(34) & Chr(34)
shell.Run command, 0, False
