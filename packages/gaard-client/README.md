# GAARD - Governed AI Access to Relational Data

GAARD is a self-hosted AI SQL Gateway for governed natural-language access to relational data.

GAARD allows applications and users to ask questions about relational databases using natural language while keeping SQL generation, validation, execution, prompts, connectors, and auditability under control.

For more informacion see https://github.com/pkroliszewski/gaard

# This package
Package gaard-client provides an implementation of chat making use of gaard-api.
After instalation, "gaard" command will be extended as "gaard client" with parameters:
--host \<ip adress\> #what adress to bind the web server to
--port\<port\> #what port to bind the web server to
--reload #whether to look for changes to the installed package

command "gaard client" by default should make the chat avaliable as http://localhost:8001?backendUrl=http://localhost:8000