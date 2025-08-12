#!/bin/bash

# Store arguments in meaningful variables
menu_app_name="$1"
file_func="$2"
sof_user_prefix="$3"

# Conditionally construct the user prefix part of the path
user_prefix_path="${sof_user_prefix:+$sof_user_prefix/}"
# Conditionally construct the app name part of the path
app_name_path="${menu_app_name:+$menu_app_name/}"

# Build the final output path
output_dir_path="../out/${user_prefix_path}${app_name_path}"

# Construct the second argument for spscript.
# This variable will be set to the combined path, but if both parts are empty,
# it will be set to '.'
spscript_arg2="${sof_user_prefix:+$sof_user_prefix/}${menu_app_name:+$menu_app_name/}"
if [[ -z "$spscript_arg2" ]]; then
  spscript_arg2=""
fi

# Now, pass these correctly-formed paths to the tool
tools/spscript "in/${file_func}" "$spscript_arg2" "$output_dir_path"