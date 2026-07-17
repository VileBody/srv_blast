#!/usr/bin/env bash
# Prod-path rollout with a FAIL-CLOSED picker readiness gate.
#
# Sourced by deploy_branch.sh. Also sourced directly by tests with a stub `docker`
# on PATH, which is why the rollout lives here and not inline in deploy_branch.sh:
# the ordering "pull -> gate -> up" is the safety property, so it has to be
# executable in isolation to be testable.
#
# THE PROPERTY: `docker compose up -d worker-build` attaches a worker to the
# user-facing `build` queue the moment it starts. So nothing may be started until
# a candidate container — same new image, --no-deps, celery command overridden —
# has proven the picker pools are healthy. On FAIL we simply never run `up -d`:
# the previously running containers keep serving the old image, Postgres is
# untouched, and the workflow goes red. That is the rollback.

DEPLOY_PICKER_READINESS_ENABLED="${DEPLOY_PICKER_READINESS_ENABLED:-true}"
# The photo flow is still behind PHOTO_FLOW_ENABLED, so a cold/thin photo pool
# reports but does not block a deploy until it is accepted.
DEPLOY_PICKER_READINESS_PHOTO_REQUIRED="${DEPLOY_PICKER_READINESS_PHOTO_REQUIRED:-false}"
# Service whose image + env the readiness candidate borrows (it is the service
# that would otherwise pick up build jobs).
PICKER_READINESS_SERVICE="${PICKER_READINESS_SERVICE:-worker-build}"

lib_is_true() {
  local v
  v="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "on" ]]
}

# Diagnostics an operator/alert needs: which node, which revision, what is
# currently serving. Printed on FAIL so the red workflow is self-explanatory.
picker_readiness_report_context() {
  local -a compose_args=("$@")
  echo "[deploy] readiness context node=${ORCHESTRATOR_NODE_NAME:-$(hostname -s 2>/dev/null || echo unknown)} revision=${BLAST_IMAGE_TAG:-unknown}"
  echo "[deploy] containers still serving (unchanged):"
  docker compose "${compose_args[@]}" ps 2>/dev/null || true
}

# Fail-closed readiness dry-run on a CANDIDATE container.
#   $@ = extra compose args (e.g. -f docker-compose.yml -f docker-compose.orchestrator-ha.yml)
# Returns 0 = PASS (safe to attach queues), non-zero = FAIL (do not switch).
picker_readiness_gate() {
  local -a compose_args=("$@")

  if ! lib_is_true "$DEPLOY_PICKER_READINESS_ENABLED"; then
    echo "[deploy] picker readiness gate DISABLED (DEPLOY_PICKER_READINESS_ENABLED=$DEPLOY_PICKER_READINESS_ENABLED)"
    return 0
  fi

  local -a check_args=(--pools video,photo)
  if lib_is_true "$DEPLOY_PICKER_READINESS_PHOTO_REQUIRED"; then
    check_args+=(--photo-required)
  fi

  echo "[deploy] picker readiness: candidate dry-run (read-only, no queues attached)"
  # --no-deps: start nothing else. --entrypoint python + explicit module: the
  # celery command in compose never runs, so this container cannot consume a job.
  # --rm: no candidate is left behind either way.
  if docker compose "${compose_args[@]}" run --rm -T --no-deps \
      --name "blast-picker-readiness-$$" \
      --entrypoint python \
      "$PICKER_READINESS_SERVICE" \
      -m services.orchestrator.picker_readiness "${check_args[@]}"; then
    echo "[deploy] picker readiness: PASS"
    return 0
  fi

  echo "[deploy] picker readiness: FAIL — refusing to attach queues or switch traffic"
  picker_readiness_report_context "${compose_args[@]}"
  return 1
}

# pull/build -> gate -> up. Never starts a service before the gate passes.
#   PROD_PATH_COMPOSE_ARGS : extra compose -f args (array, may be empty)
#   PROD_PATH_SERVICES     : services to roll out (array)
#   PROD_PATH_USE_PREBUILT : true => pull, false => build
prod_path_rollout() {
  # NB: "${arr[@]:-}" would expand an EMPTY array to one empty-string argument,
  # i.e. `docker compose "" pull ...`. Test for "set" instead so the non-HA path
  # (which has no extra -f files) passes no argument at all.
  local -a compose_args=()
  if [[ -n "${PROD_PATH_COMPOSE_ARGS+x}" ]]; then
    compose_args=("${PROD_PATH_COMPOSE_ARGS[@]}")
  fi
  local -a services=("${PROD_PATH_SERVICES[@]}")

  if [[ ${#services[@]} -eq 0 ]]; then
    echo "[deploy] prod_path_rollout: no services specified"
    return 1
  fi

  # Stage the new image WITHOUT starting anything.
  if lib_is_true "${PROD_PATH_USE_PREBUILT:-false}"; then
    echo "[deploy] docker compose pull ${services[*]}"
    docker compose "${compose_args[@]}" pull "${services[@]}" || return 1
  else
    echo "[deploy] docker compose build ${services[*]}"
    docker compose "${compose_args[@]}" build "${services[@]}" || return 1
  fi

  # Gate: the candidate proves the pools before any worker can take a job.
  picker_readiness_gate "${compose_args[@]}" || return 1

  echo "[deploy] docker compose up -d --no-build ${services[*]}"
  docker compose "${compose_args[@]}" up -d --no-build "${services[@]}" || return 1
}
