$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$audioDirectory = Join-Path $PSScriptRoot 'audio'
New-Item -ItemType Directory -Path $audioDirectory -Force | Out-Null
$videoPlan = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voice.SelectVoice('Microsoft Zira Desktop')
    $voice.Rate = 1
    $voice.Volume = 100
    foreach ($scene in $videoPlan) {
        $voice.SetOutputToWaveFile((Join-Path $audioDirectory ($scene.id + '.wav')))
        $voice.Speak($scene.narration)
        $voice.SetOutputToNull()
        Write-Output ('Narration ready: ' + $scene.id)
    }
} finally { $voice.Dispose() }
$mediaPython = Join-Path (Split-Path $PSScriptRoot -Parent) 'langgraph_impl\.venv\Scripts\python.exe'
@'
import json,sys,wave
from pathlib import Path
root=Path(sys.argv[1]);durations={}
for scene in json.loads((root/'plan.json').read_text(encoding='utf-8')):
    with wave.open(str(root/'audio'/(scene['id']+'.wav')),'rb') as sound:
        durations[scene['id']]=sound.getnframes()/sound.getframerate()
(root/'audio/durations.json').write_text(json.dumps(durations,indent=2),encoding='utf-8')
'@ | & $mediaPython -X utf8 - $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not measure narration durations' }
