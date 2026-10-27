"""Local prototype launcher. Credentials stay in process memory, never in UI or files."""
import os
import subprocess
import sys
from pathlib import Path
import yaml

root = Path(__file__).resolve().parent
env = os.environ.copy()
if not env.get('OPENAI_API_KEY'):
    config = yaml.safe_load((Path.home()/'.local/share/cliproxyapi/config.yaml').read_text())
    env['OPENAI_API_KEY'] = config['api-keys'][0]
env.setdefault('OPENAI_BASE_URL', 'http://127.0.0.1:8317/v1')
if '--pulse-voice' in sys.argv and not env.get('DEEPGRAM_API_KEY'):
    known = str(Path.home()/'Documents/ChatGPT/mashadasha-production-access-20261020/private/known_hosts')
    key = str(Path.home()/'.ssh/yandex_ebitrix')
    proxy = f'ssh -o ProxyCommand=none -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="{known}" -i "{key}" -W %h:%p nikita@158.160.179.53'
    script = "from pathlib import Path\nfor line in Path('/etc/pulse-intake/app.env').read_text().splitlines():\n if line.startswith('DEEPGRAM_API_KEY='): print(line.partition('=')[2].strip().strip(chr(34)).strip(chr(39)))\n"
    try:
        # The project file pins the gateway; the already trusted private host is in known_hosts.
        target_known = str(Path.home()/'.ssh/known_hosts')
        result = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes','-o',f'UserKnownHostsFile={target_known}','-o',f'ProxyCommand={proxy}','-i',key,'nikita@10.130.0.18','sudo python3 -'], input=script, capture_output=True, text=True, timeout=25)
        if result.returncode == 0 and result.stdout.strip():
            env['DEEPGRAM_API_KEY'] = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
print('Vacancy studio: model credential ready; voice configured =', bool(env.get('DEEPGRAM_API_KEY')), flush=True)
os.execve(sys.executable, [sys.executable, str(root/'server.py')], env)
