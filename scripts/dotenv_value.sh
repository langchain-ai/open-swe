#!/bin/sh
# Print the value of KEY from a dotenv FILE the way python-dotenv reads it: last
# assignment wins, surrounding quotes are removed, and an unquoted value ends at the
# first whitespace or '#'. Prints nothing when the file or key is missing.
# Usage: dotenv_value.sh FILE KEY
file=$1
key=$2
[ -r "$file" ] || exit 0
sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$key=//p" "$file" | tail -n 1 | sed -E \
  -e 's/^"([^"]*)".*$/\1/' \
  -e "s/^'([^']*)'.*\$/\1/" \
  -e 's/^([^"'"'"'][^[:space:]#]*).*$/\1/' \
  -e 's/^[[:space:]#].*$//'
