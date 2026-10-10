Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
WshShell.CurrentDirectory = "C:\Users\pasum\swagbucks_multi_tracker"
pyw = "C:\Users\pasum\AppData\Local\Programs\Python\Python314\pythonw.exe"
If fso.FileExists("C:\Users\pasum\swagbucks_multi_tracker\.venv\Scripts\pythonw.exe") Then
    pyw = "C:\Users\pasum\swagbucks_multi_tracker\.venv\Scripts\pythonw.exe"
End If
WshShell.Run """" & pyw & """ ""C:\Users\pasum\swagbucks_multi_tracker\service_runner.py""", 0, False
