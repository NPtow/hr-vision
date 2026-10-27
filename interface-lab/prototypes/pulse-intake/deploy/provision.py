"""Run as root on outreach. Reads existing authorized service keys without printing them."""
from pathlib import Path
import os
import secrets
import uuid

def env_file(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip().strip('\"').strip("'")
    return result

def main():
    os.umask(0o077)
    target = Path('/etc/pulse-intake/app.env')
    if target.exists():
        print('Environment already provisioned; existing access retained.')
        return
    hr, voice = env_file('/etc/hr-vision/api.env'), env_file('/etc/oasis/env')
    values = {
        'OPENAI_BASE_URL': hr['HR_LLM_BASE_URL'],
        'OPENAI_API_KEY': hr['HR_LLM_API_KEY'],
        'DEEPGRAM_API_KEY': voice['DEEPGRAM_API_KEY'],
        'INTAKE_MODEL': 'gpt-6-luna',
        'INTAKE_PORT': '8392',
        'INTAKE_STATE_PATH': '/state/state.sqlite3',
        'INTAKE_PUBLIC_ORIGIN': 'https://pulse.158-160-179-53.sslip.io',
        'INTAKE_INVITE_KEY': secrets.token_urlsafe(32),
        'INTAKE_INVITE_SESSION': str(uuid.uuid4()),
        'INTAKE_TEST_KEY': secrets.token_urlsafe(32),
        'INTAKE_TEST_SESSION': str(uuid.uuid4()),
    }
    if any('\n' in value or '\r' in value for value in values.values()):
        raise ValueError('Invalid environment value')
    target.parent.mkdir(mode=0o700, exist_ok=True)
    target.write_text(''.join(key+'='+value+'\n' for key,value in values.items()))
    target.chmod(0o600)
    state = Path('/var/lib/pulse-intake')
    state.mkdir(mode=0o700, exist_ok=True)
    os.chown(state, 10001, 10001)
    print('Environment and private persistent state provisioned.')

if __name__ == '__main__':
    main()
