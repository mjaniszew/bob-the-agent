---
name: playwright
description: Visit websites and interact with them using playwright
---

# Playwright Skill

Use this skill to open and visit complex websites, interact with graphical interfaces of websites, take screenshots, and perform web automations.

## When to Use

Use this skill when:
- You are specifically asked to open, visit websites using playwright
- Use browser for JavaScript-heavy sites that WebFetch cannot parse for any reasons
- You need to access graphical interface of an website
- You need to execute scripts in a browser
- You need to take screenshots of websites
- You need to perform website automations
- Take screenshots for visual evidence in research reports

## Usage

Call the help command first in order to get the list of available commands and their usage.:

```bash
playwright-cli --help
```

Then call desired command:
### Core

```bash
playwright-cli open [url]               # open browser, optionally navigate to url
playwright-cli goto <url>               # navigate to a url
playwright-cli close                    # close the page
playwright-cli type <text>              # type text into editable element
playwright-cli click <ref> [button]     # perform click on a web page
playwright-cli dblclick <ref> [button]  # perform double click on a web page
playwright-cli fill <ref> <text>        # fill text into editable element
playwright-cli fill <ref> <text> --submit # fill and press Enter
playwright-cli drag <startRef> <endRef> # perform drag and drop between two elements
playwright-cli hover <ref>              # hover over element on page
playwright-cli select <ref> <val>       # select an option in a dropdown
playwright-cli upload <file>            # upload one or multiple files
playwright-cli check <ref>              # check a checkbox or radio button
playwright-cli uncheck <ref>            # uncheck a checkbox or radio button
playwright-cli snapshot                 # capture page snapshot to obtain element ref
playwright-cli snapshot --filename=f    # save snapshot to specific file
playwright-cli snapshot <ref>           # snapshot a specific element
playwright-cli snapshot --depth=N       # limit snapshot depth for efficiency
playwright-cli eval <func> [ref]        # evaluate javascript expression on page or element
playwright-cli dialog-accept [prompt]   # accept a dialog
playwright-cli dialog-dismiss           # dismiss a dialog
playwright-cli resize <w> <h>           # resize the browser window
```

### Navigation

```bash
playwright-cli go-back                  # go back to the previous page
playwright-cli go-forward               # go forward to the next page
playwright-cli reload                   # reload the current page
```

### Keyboard

```bash
playwright-cli press <key>              # press a key on the keyboard, `a`, `arrowleft`
playwright-cli keydown <key>            # press a key down on the keyboard
playwright-cli keyup <key>              # press a key up on the keyboard
```

### Mouse

```bash
playwright-cli mousemove <x> <y>        # move mouse to a given position
playwright-cli mousedown [button]       # press mouse down
playwright-cli mouseup [button]         # press mouse up
playwright-cli mousewheel <dx> <dy>     # scroll mouse wheel
```

### Save as

```bash
playwright-cli screenshot [ref]         # screenshot of the current page or element
playwright-cli screenshot --filename=f  # save screenshot with specific filename
playwright-cli pdf                      # save page as pdf
playwright-cli pdf --filename=page.pdf  # save pdf with specific filename
```

### Tabs

```bash
playwright-cli tab-list                 # list all tabs
playwright-cli tab-new [url]            # create a new tab
playwright-cli tab-close [index]        # close a browser tab
playwright-cli tab-select <index>       # select a browser tab
```

### Storage

```bash
playwright-cli state-save [filename]    # save storage state
playwright-cli state-load <filename>    # load storage state

# Cookies
playwright-cli cookie-list [--domain]   # list cookies
playwright-cli cookie-get <name>        # get a cookie
playwright-cli cookie-set <name> <val>  # set a cookie
playwright-cli cookie-delete <name>     # delete a cookie
playwright-cli cookie-clear             # clear all cookies

# LocalStorage
playwright-cli localstorage-list        # list localStorage entries
playwright-cli localstorage-get <key>   # get localStorage value
playwright-cli localstorage-set <k> <v> # set localStorage value
playwright-cli localstorage-delete <k>  # delete localStorage entry
playwright-cli localstorage-clear       # clear all localStorage

# SessionStorage
playwright-cli sessionstorage-list      # list sessionStorage entries
playwright-cli sessionstorage-get <k>   # get sessionStorage value
playwright-cli sessionstorage-set <k> <v> # set sessionStorage value
playwright-cli sessionstorage-delete <k>  # delete sessionStorage entry
playwright-cli sessionstorage-clear     # clear all sessionStorage
```

### Network

```bash
playwright-cli route <pattern> [opts]   # mock network requests
playwright-cli route-list               # list active routes
playwright-cli unroute [pattern]        # remove route(s)
```

### DevTools

```bash
playwright-cli console [min-level]      # list console messages
playwright-cli network                  # list all network requests since loading the page
playwright-cli run-code <code>          # run playwright code snippet
playwright-cli run-code --filename=f    # run playwright code from a file
playwright-cli tracing-start            # start trace recording
playwright-cli tracing-stop             # stop trace recording
playwright-cli video-start [filename]   # start video recording
playwright-cli video-chapter <title>    # add a chapter marker to the video
playwright-cli video-stop               # stop video recording
```

### Open parameters

```bash
playwright-cli open --browser=chrome    # use specific browser
playwright-cli open --extension         # connect via browser extension
playwright-cli open --persistent        # use persistent profile
playwright-cli open --profile=<path>    # use custom profile directory
playwright-cli open --config=file.json  # use config file
playwright-cli close                    # close the browser
playwright-cli delete-data              # delete user data for default session
```

## Always follow these rules

- Always double check comamnd by calling `playwright-cli --help` with the first website visit within task, and if you have any doubts, check playwright-cli documentation available here: https://github.com/microsoft/playwright-cli/blob/main/README.md
- Use tabs when within one task you're planning to access multiple websites
- After completing task, always close browser with `playwright-cli close`, and delete user data with `playwright-cli delete-data`

## Examples

### Discover all commands
```bash
playwright-cli --help
```

### Open browser and navigate to Website
```bash
playwright-cli open https://example.com
```

### Open Website in existing browser, take and save screenshot
```bash
playwright-cli goto https://example.com
playwright-cli screenshot #body
playwright-cli screenshot --filename=/app/workspace/{agent}/screenshot/{website}/{date:YYYY-MM-DD}-{time:HH-mm}.png
```

### Close browser
```bash
playwright-cli close
```
