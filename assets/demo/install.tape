Output assets/demo/install.gif

Set Shell "bash"
Set FontSize 18
Set Width 1120
Set Height 760
Set Padding 20
Set Framerate 10
Set TypingSpeed 35ms
Set Theme "Catppuccin Mocha"

Hide
Type "export PS1='$ ' && export PROMPT_COMMAND= && export PATH=/vhs/assets/demo/bin:$PATH && clear"
Enter
Show

Type "npx --yes --package claude-anyteam claude-anyteam-setup"
Enter
Sleep 15s
