# dynamic_sof_apps
server side app/menu store clients can keep up to date whilst in-game

# regarding python rmf parser ( .rmf side of things )



# regarding spscript tool ( .func side of things )
## ~~info~~
* ~~The `cvarify_func.sh` script creates a `.cfg` file for each function() defined in the input file.~~
* ~~Every scope { } is saved in its own cvar. And attached to the sp_sc_flow_if executor.~~
* ~~Everything is called by sp_sc_exec_cvar , there was no sp_sc_func .. functions~~
* ~~This is how we called functions back then...~~
* ~~sp_sc_alias YOUR_FUNC `sset ~tmp_args ${@}; sp_sc_exec_cvar your_func`  ~~
* ~~so you _ENSURE_ the sp_sc_alias is setup in the _init function, then use the alias to call the function. accessing the local variable written there in the alias.~~
* ~~this mean sp_sc_func_exec will not work , hmm, because the func isn't loaded.~~
## ~~usage~~
 ~~**ARGS TO THIS SCRIPT** - do not trail slash on dirs~~
`[MENU_APP_NAME] [FILE.FUNC] [sof_user_prefix]`

~~eg 1.  ~~
`./cvarify_func.sh lean test.func dynamic/cache`  

~~eg. 2.  ~~
`./cvarify_func.sh "" test.func ""`  


