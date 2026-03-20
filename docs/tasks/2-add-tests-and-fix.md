# Add tests and fix

Current solution is failing during docker compose up step, and tehere's couple of issues I've already spotted. Do the following tasks:
1. Change base image in Dockerfiles to one using node 24 since it's current lts version in 2026, and it's recommended by openclaw. Use 24.14.0-bookworm image or similar
2. Reverify all commands, scripts, and codebase for up to date practices and libraries version from this year, it's march 2026. Do necessary changes and edits without asking.
3. Add proper tests for setting up entire stack via docker compose
4. Test whether docker compose actually works by exxecuting it. Current system has docker installed so it's doable, but do it in any way you consider proper and safe
5. Check all errors that will come up when building containers, and fix them. Every time issue will be spotted, find silution for it, and retest with building container again. Iterate until all issues will be solved 
