# Sourced by scripts/demo.sh. Works with bash 3.2 (macOS) and GNU bash (Linux).
#
# load_env_file FILE: export the variables of a .env file, but only those that the
# environment does not set already, so `JEV_MODE=stub scripts/demo.sh` wins over a .env that
# sets JEV_MODE. A variable that is set but empty counts as set.
# The file is sourced once, as before (that is what a .env implies); values are never
# printed and never passed to eval. Values set before are put back with `export NAME=value`.
load_env_file() {
    local file="$1" name i
    local names=() vals=()
    [ -f "$file" ] || return 0
    # Names of assignments in the file: NAME=... or export NAME=...
    for name in $(sed -n 's/^[[:space:]]*\(export[[:space:]][[:space:]]*\)\{0,1\}\([A-Za-z_][A-Za-z0-9_]*\)=.*/\2/p' "$file"); do
        if [ -n "${!name+x}" ]; then
            names+=("$name")
            vals+=("${!name}")
        fi
    done
    set -a
    # shellcheck disable=SC1090
    . "$file"
    set +a
    for i in ${names[@]+"${!names[@]}"}; do
        export "${names[$i]}=${vals[$i]}"
    done
}
