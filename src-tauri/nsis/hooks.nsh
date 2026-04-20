!macro StopSciPilotProcesses
  DetailPrint "Stopping running SciPilot processes..."

  nsExec::ExecToStack '"$SYSDIR\taskkill.exe" /IM "scipilot.exe" /F /T'
  Pop $0
  Pop $1

  nsExec::ExecToStack '"$SYSDIR\taskkill.exe" /IM "scipilot-sidecar.exe" /F /T'
  Pop $0
  Pop $1

  Sleep 1500
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro StopSciPilotProcesses
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro StopSciPilotProcesses
!macroend
