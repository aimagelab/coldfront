if [ $# -lt 1 ]; then
    echo "Syntax: $_ USER GROUP" >&2
    exit 1
fi

exit_code=0
user=$1
group=$2

echo $user
echo $group

home="/homes/$user"
# If home already exists, nothing to do
if [ -d "$home" ]; then
    echo $'\e[32m''Home already exists: '"$home"$'\e[m'
    exit 0
fi

# Ensure home exists before populating
mkdir -p "$home" && echo $'\e[32m''Created home: '"$home"$'\e[m' || ( exit_code=$?; echo $'\e[31m''Failed to create: '"$home"$'\e[m' ) >&2
chmod 700 "$home" && echo $'\e[32m'"Set permission on: $home"$'\e[m' || ( exit_code=$?; echo $'\e[31m'"Failed to set permission on: $home"$'\e[m' ) >&2
cp -R ./skel/. "$home" && echo $'\e[32m'"Copied skeleton to: $home"$'\e[m' || ( exit_code=$?; echo $'\e[31m'"Failed to create: $home"$'\e[m' ) >&2
chown -R "$user:$group" "$home" && echo $'\e[32m'"Set owner on: $home"$'\e[m' || ( exit_code=$?; echo $'\e[31m'"Failed to set owner on: $home"$'\e[m' ) >&2
exit $exit_code
