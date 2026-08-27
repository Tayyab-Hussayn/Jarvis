$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('C:\Users\ravit\OneDrive\Desktop\Kravein Ai - Premium.lnk')
$Shortcut.TargetPath = 'D:\TiTech Prabha Solution\Kravein AI\Kravein AI\Kravein-AI---Lite-main\Kravein-AI---Lite-main\.venv\Scripts\pythonw.exe'
$Shortcut.Arguments = '"D:\TiTech Prabha Solution\Kravein AI\Kravein AI\Kravein-AI---Lite-main\Kravein-AI---Lite-main\main.py"'
$Shortcut.WorkingDirectory = 'D:\TiTech Prabha Solution\Kravein AI\Kravein AI\Kravein-AI---Lite-main\Kravein-AI---Lite-main'
$Shortcut.WindowStyle = 7
$Shortcut.Description = 'Launch Kravein Ai - Premium'
if ('D:\TiTech Prabha Solution\Kravein AI\Kravein AI\Kravein-AI---Lite-main\Kravein-AI---Lite-main\assets\Kravein_Lite_Logo.ico') { $Shortcut.IconLocation = 'D:\TiTech Prabha Solution\Kravein AI\Kravein AI\Kravein-AI---Lite-main\Kravein-AI---Lite-main\assets\Kravein_Lite_Logo.ico,0' }
$Shortcut.Save()