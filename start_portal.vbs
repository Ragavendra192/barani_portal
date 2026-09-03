Set WshShell = CreateObject("WScript.Shell")
Dim fso, scriptDir, pythonExe, appPath
Set fso = CreateObject("Scripting.FileSystemObject")

' Get the directory of the VBScript file
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
appPath = scriptDir & "\app.py"

' Set working directory
On Error Resume Next
WshShell.CurrentDirectory = scriptDir
On Error GoTo 0

' Function to test if a Python command actually executes on this computer
Function IsValidPython(cmdStr)
    On Error Resume Next
    Dim execObj
    Set execObj = WshShell.Exec(cmdStr & " -c ""import sys; sys.exit(0)""")
    If Err.Number <> 0 Then
        IsValidPython = False
        On Error GoTo 0
        Exit Function
    End If
    Dim count
    count = 0
    Do While execObj.Status = 0 And count < 20
        WScript.Sleep 50
        count = count + 1
    Loop
    If execObj.Status = 1 And execObj.ExitCode = 0 Then
        IsValidPython = True
    Else
        IsValidPython = False
    End If
    On Error GoTo 0
End Function

' Function to locate Python dynamically
Function FindPython()
    Dim localAppData, progFiles, folder, subFolder, candidate
    
    ' 1. Check local virtual environment (venv) if valid on this machine
    If fso.FileExists(scriptDir & "\venv\Scripts\pythonw.exe") Then
        candidate = """" & scriptDir & "\venv\Scripts\pythonw.exe"""
        If IsValidPython(candidate) Then
            FindPython = candidate
            Exit Function
        End If
    End If
    If fso.FileExists(scriptDir & "\venv\Scripts\python.exe") Then
        candidate = """" & scriptDir & "\venv\Scripts\python.exe"""
        If IsValidPython(candidate) Then
            FindPython = candidate
            Exit Function
        End If
    End If

    ' 2. Check user AppData Python installations (e.g. C:\Users\...\AppData\Local\Programs\Python\Python311)
    localAppData = WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
    If localAppData <> "" And localAppData <> "%LOCALAPPDATA%" Then
        If fso.FolderExists(localAppData & "\Programs\Python") Then
            Set folder = fso.GetFolder(localAppData & "\Programs\Python")
            For Each subFolder In folder.SubFolders
                If fso.FileExists(subFolder.Path & "\pythonw.exe") Then
                    candidate = """" & subFolder.Path & "\pythonw.exe"""
                    If IsValidPython(candidate) Then FindPython = candidate: Exit Function
                ElseIf fso.FileExists(subFolder.Path & "\python.exe") Then
                    candidate = """" & subFolder.Path & "\python.exe"""
                    If IsValidPython(candidate) Then FindPython = candidate: Exit Function
                End If
            Next
        End If
    End If

    ' 3. Check Program Files
    progFiles = WshShell.ExpandEnvironmentStrings("%ProgramFiles%")
    If fso.FolderExists(progFiles) Then
        Set folder = fso.GetFolder(progFiles)
        For Each subFolder In folder.SubFolders
            If LCase(Left(subFolder.Name, 6)) = "python" Then
                If fso.FileExists(subFolder.Path & "\pythonw.exe") Then
                    candidate = """" & subFolder.Path & "\pythonw.exe"""
                    If IsValidPython(candidate) Then FindPython = candidate: Exit Function
                ElseIf fso.FileExists(subFolder.Path & "\python.exe") Then
                    candidate = """" & subFolder.Path & "\python.exe"""
                    If IsValidPython(candidate) Then FindPython = candidate: Exit Function
                End If
            End If
        Next
    End If

    ' 4. Check C:\ root Python installations (e.g. C:\Python311)
    If fso.FolderExists("C:\") Then
        Set folder = fso.GetFolder("C:\")
        For Each subFolder In folder.SubFolders
            If LCase(Left(subFolder.Name, 6)) = "python" Then
                If fso.FileExists(subFolder.Path & "\pythonw.exe") Then
                    candidate = """" & subFolder.Path & "\pythonw.exe"""
                    If IsValidPython(candidate) Then FindPython = candidate: Exit Function
                ElseIf fso.FileExists(subFolder.Path & "\python.exe") Then
                    candidate = """" & subFolder.Path & "\python.exe"""
                    If IsValidPython(candidate) Then FindPython = candidate: Exit Function
                End If
            End If
        Next
    End If

    ' 5. Try standard system PATH
    If IsValidPython("pythonw.exe") Then
        FindPython = "pythonw.exe"
        Exit Function
    ElseIf IsValidPython("python.exe") Then
        FindPython = "python.exe"
        Exit Function
    End If

    ' Fallback
    FindPython = ""
End Function

pythonExe = FindPython()

' Verify Python was found
If pythonExe = "" Then
    WshShell.Popup "Error: Python executable could not be found or executed on this server. Please install Python or rebuild the virtual environment (venv) on this laptop.", 10, "BHIPL Portal Error", 16
    WScript.Quit 1
End If

' Verify app.py exists before launching
If Not fso.FileExists(appPath) Then
    WshShell.Popup "Error: Could not find app.py at " & appPath, 10, "BHIPL Portal Error", 16
    WScript.Quit 1
End If

' Ensure Autostart Shortcut is in Windows Startup Folder
Dim startupFolder, shortcutPath, shortcut
startupFolder = WshShell.SpecialFolders("Startup")
shortcutPath = startupFolder & "\BHIPLPortal.lnk"

If Not fso.FileExists(shortcutPath) Then
    On Error Resume Next
    Set shortcut = WshShell.CreateShortcut(shortcutPath)
    shortcut.TargetPath = "wscript.exe"
    shortcut.Arguments = """" & WScript.ScriptFullName & """"
    shortcut.WorkingDirectory = scriptDir
    shortcut.Description = "Starts the BHIPL Portal in the background"
    shortcut.Save
    If Err.Number = 0 Then
        WshShell.Popup "BHIPL Portal has been configured to auto-start on Windows login.", 5, "Autostart Configured", 64
    End If
    On Error GoTo 0
End If

' Run the application silently in the background with full paths
' 0 = Hide the window
' False = Do not wait for script to finish
WshShell.Run pythonExe & " """ & appPath & """", 0, False

Set WshShell = Nothing
Set fso = Nothing
