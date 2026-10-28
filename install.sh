#!/usr/bin/env bash
# Install only the management CLI. Node provisioning is an explicit ncloud init.
set -euo pipefail
umask 077

if [[ $(uname -s) != Linux || ${EUID} -ne 0 ]]; then
  echo 'Run on the Linux VPS with sudo bash install.sh.' >&2
  exit 1
fi
if [[ ! -d /run/systemd/system ]]; then
  echo 'A running systemd instance is required.' >&2
  exit 1
fi
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ! -f "$source_dir/ncloud/cli.py" ]]; then
  echo 'Incomplete checkout; clone the complete Git repository.' >&2
  exit 1
fi
if [[ "$source_dir" == /opt/ncloud ]]; then
  echo 'Run install.sh from your Git checkout, not /opt/ncloud.' >&2
  exit 1
fi
find_python() {
  local candidate
  for candidate in python3.11 python3.12 python3.13 python3.9 python3.8 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}
python_bin=$(find_python || true)
if [[ -z "$python_bin" ]]; then
  if command -v dnf >/dev/null 2>&1; then
    for package in python3.11 python39 python3; do
      if dnf -y install "$package"; then
        python_bin=$(find_python || true)
        [[ -n "$python_bin" ]] && break
      fi
    done
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3 ca-certificates
    python_bin=$(find_python || true)
  fi
fi
if [[ -z "$python_bin" ]]; then
  echo 'Install Python >= 3.8 from a supported OS repository, then rerun.' >&2
  exit 1
fi
for tool in systemctl useradd; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing required OS command: $tool" >&2
    exit 1
  fi
done
for target in /opt/ncloud /usr/local/bin/ncloud; do
  if [[ -L "$target" ]]; then
    echo "Refusing symlink target: $target" >&2
    exit 1
  fi
done
if [[ -e /opt/ncloud && ! -f /opt/ncloud/.ncloud-managed ]]; then
  echo '/opt/ncloud exists and is not managed by this installer.' >&2
  exit 1
fi
if [[ -e /usr/local/bin/ncloud ]] && ! grep -q '^# Managed by ncloud$' /usr/local/bin/ncloud; then
  echo '/usr/local/bin/ncloud already belongs to another program.' >&2
  exit 1
fi
"$python_bin" -m unittest discover -s "$source_dir/tests" -t "$source_dir" -q
mkdir -p /opt /usr/local/bin
stage_dir=$(mktemp -d /opt/.ncloud-stage.XXXXXX)
backup_dir=''
success=0
cleanup() {
  rm -rf -- "$stage_dir"
  if [[ $success -eq 0 && -n "$backup_dir" && -d "$backup_dir" ]]; then
    rm -rf -- /opt/ncloud
    mv -- "$backup_dir" /opt/ncloud
  elif [[ -n "$backup_dir" && -d "$backup_dir" ]]; then
    rm -rf -- "$backup_dir"
  fi
}
trap cleanup EXIT
mkdir -p "$stage_dir/ncloud"
cp "$source_dir"/ncloud/*.py "$stage_dir/ncloud/"
cp "$source_dir/README.md" "$source_dir/LICENSE" "$stage_dir/"
printf 'ncloud management CLI\n' > "$stage_dir/.ncloud-managed"
chmod -R u=rwX,go=rX "$stage_dir"
if [[ -d /opt/ncloud ]]; then
  backup_dir=$(mktemp -d /opt/.ncloud-old.XXXXXX)
  rmdir -- "$backup_dir"
  mv -- /opt/ncloud "$backup_dir"
fi
mv -- "$stage_dir" /opt/ncloud
launcher=$(mktemp /usr/local/bin/.ncloud.XXXXXX)
cat > "$launcher" <<EOF
#!/usr/bin/env bash
# Managed by ncloud
set -euo pipefail
exec "$python_bin" -I -c 'import sys; sys.path.insert(0,"/opt/ncloud"); from ncloud.cli import main; sys.exit(main())' "\$@"
EOF
chmod 0755 "$launcher"
mv -f -- "$launcher" /usr/local/bin/ncloud
success=1
echo 'ncloud installed. Next: sudo ncloud init --server YOUR_PUBLIC_IPV4'
