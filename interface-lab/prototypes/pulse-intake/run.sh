#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  exec ruby -ryaml -e '
    config = YAML.load_file(File.expand_path("~/.local/share/cliproxyapi/config.yaml"))
    ENV["OPENAI_API_KEY"] = Array(config["api-keys"]).first
    ENV["OPENAI_BASE_URL"] ||= "http://127.0.0.1:8317/v1"
    exec("python3", "server.py")
  '
fi
exec python3 server.py
