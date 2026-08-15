#!/usr/bin/env bash
# live_deploy — fetches this app's 4 required credentials from AWS
# Systems Manager Parameter Store (SecureString) and execs the given
# command with them set as environment variables. Nothing plaintext
# ever touches disk on the instance: config.json isn't needed at all
# (load_config() already treats it as fully optional once every
# required key is covered by an env var — see app/config.py), and
# these values only ever exist in this process's own environment,
# fetched fresh on every start.
#
# This is the AWS-native "real secrets manager" step RUN_GUIDE.md's own
# Credential hardening section pointed at: "only worth building against
# a specific hosting target once one's actually chosen" — AWS is now
# that target.
#
# Requires:
#   - the AWS CLI (`aws`) on PATH
#   - the instance's IAM role (or whatever credentials the CLI picks
#     up) granted ssm:GetParameters on the parameter path used below —
#     see RUN_GUIDE.md's "AWS deployment: SSM Parameter Store" section
#     for the one-time `aws ssm put-parameter` setup and a
#     least-privilege IAM policy scoped to just this path.
#
# Usage:
#   ./fetch_ssm_secrets.sh python3 run.py
#   ./fetch_ssm_secrets.sh uvicorn app.main:app --host 0.0.0.0 --port 8000
#
# SSM_PARAM_PREFIX (env var, optional) overrides the default parameter
# path prefix below — lets one AWS account host more than one
# environment (staging/prod) under different prefixes without editing
# this script.
set -euo pipefail

PREFIX="${SSM_PARAM_PREFIX:-/live-deploy}"

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <command> [args...]" >&2
  exit 1
fi

# One API call for all four, not four separate ones — cheaper, and a
# missing/misnamed parameter is caught right here with a clear message
# instead of surfacing later as a confusing crash inside load_config().
RESULT=$(aws ssm get-parameters \
  --with-decryption \
  --names "$PREFIX/KITE_API_KEY" "$PREFIX/KITE_API_SECRET" "$PREFIX/DATABASE_URL" "$PREFIX/APP_AUTH_SECRET" \
  --query 'Parameters[*].[Name,Value]' \
  --output text)

if [ -z "$RESULT" ]; then
  echo "fetch_ssm_secrets.sh: no parameters found under $PREFIX -- check SSM_PARAM_PREFIX and the instance's IAM role" >&2
  exit 1
fi

while IFS=$'\t' read -r name value; do
  case "$name" in
    "$PREFIX/KITE_API_KEY") export KITE_API_KEY="$value" ;;
    "$PREFIX/KITE_API_SECRET") export KITE_API_SECRET="$value" ;;
    "$PREFIX/DATABASE_URL") export DATABASE_URL="$value" ;;
    "$PREFIX/APP_AUTH_SECRET") export APP_AUTH_SECRET="$value" ;;
  esac
done <<< "$RESULT"

MISSING=""
for var in KITE_API_KEY KITE_API_SECRET DATABASE_URL APP_AUTH_SECRET; do
  if [ -z "${!var:-}" ]; then
    MISSING="$MISSING $var"
  fi
done
if [ -n "$MISSING" ]; then
  echo "fetch_ssm_secrets.sh: missing from SSM under $PREFIX:$MISSING" >&2
  exit 1
fi

echo "fetch_ssm_secrets.sh: fetched 4/4 secrets from SSM ($PREFIX), launching: $*" >&2
exec "$@"
