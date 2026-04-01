# Tools

This file describes which additional tools should be configure or added to the agent in ordder to enable agent and it's subagent to perform certain tasks.

## Agent Browser
1. Agent should be able to freely use headelss browser in order to visit websites and interact with them, extract data from them, take screenshots etc.
2. Headless browser should be deployed as separate container and available from agent container over defined port.
3. Agent Browser Container should have healtheck along with simple tool checking whether browser is accessible and working correctly, and if it is not, it should be restarted.
4. Agent Browser should be integrated with openclaw directly using built-in capabilities if possible.
