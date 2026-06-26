# gaard-plugin-api

`gaard-plugin-api` provides the stable, dependency-light contracts used by
GAARD extension packages. It defines extension manifests, discovery through
Python entry points, compatibility validation, and contribution activation.

An extension is trusted, installed Python code. GAARD does not load executable
extensions from database records, configuration files, or arbitrary URLs.
