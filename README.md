# Odoo Desktop

A Windows app to work on your Odoo tasks without opening the browser: see what needs attention, read and move tasks, reply, log time and plan your day.

## Open the app

Double-click **`OdooDesktop.exe`** in this folder. Nothing else to install.

Sign in with:

| Field | What to enter |
|---|---|
| Server | e.g. `your-company.odoo.com` (`https://` is added for you) |
| Database | Your Odoo database name. Optional only if the server lists exactly one database; many servers do not list them, so enter it. |
| Email | your Odoo login |
| Password or API key | Your normal Odoo password, or an API key (Odoo → avatar → My Profile / Preferences → Account Security → New API Key). Accounts with two-step verification must use an API key: Odoo refuses the password for app connections. |

Server, database, email and preferences are remembered in `%APPDATA%\OdooDesktop\settings.json`.

**Stay signed in** keeps your password or API key in Windows Credential Manager (never in a file) and signs you in automatically next time. Signing out, or unticking it in Settings, removes it.

## Pages

The app loads every task assigned to you, across all projects, and refreshes every 5 minutes.

- **Overview** — counters, quick actions and the tasks that need attention now. **New task** creates a task assigned to you in a project you choose.
- **Today** — your plan for the day. Add tasks from the suggestions, from Focus or from a task page, tick them off, and what is left is carried over to the next day. Stored on this computer only.
- **Focus** — one list for all projects, grouped by urgency. Search, filter by project, pinned, untouched or done; export to CSV.
- **Calendar** — deadlines on a month grid. Click a task to read it, or "+n more" to list that day in Focus.
- **Mentions** — messages that tagged you. Tick each one off yourself when you have dealt with it (click its status, or select several and click Mark checked); the list also shows if you already replied. Stored on this computer only.
- **What changed** — what happened to your tasks since the last time you opened the app.
- **Project health** — one bar per project, with a Late / At risk / On track label, based on your own tasks.
- **Team** — everyone's open tasks in one project, grouped by person, most overdue first.
- **Weekly summary** — finished, moved, new, rescheduled and slipped tasks for the current week. Copy or save it as text.
- **Settings** — notifications, tray, stay signed in, theme, data folder.

### The task page

Double-click a task anywhere to read it in full: description, checklists, sub-tasks, attachments, messages and properties. From there you can:

- **Move to stage** — pick a stage of the project and click Move.
- **Reply** — write at the bottom and click Send; it is posted as a comment and Odoo notifies the followers.
- **Timer** — Start, then Stop to log the time as a timesheet line (hours and description can be edited first). The timer survives closing the app.
- **Today / Pin / private note** — local helpers, never sent to Odoo.
- **Attachments** — click a file to download and open it. Small PNG and GIF pictures are shown in the page. Programs and scripts are never started: they are only shown in Explorer.

Every action that writes to Odoo asks for confirmation first.

### Notifications and tray

With the options in Settings turned on, closing the window keeps the app next to the clock (right-click the icon to quit) and Windows shows a notification when a task is assigned to you, a deadline is today, or someone replies on one of your tasks.

### Good to know

- **Done tasks** — a task counts as done when Odoo marks it closed, when its stage is folded in the kanban, or when the stage name contains a word like Done or Cancelled. Done tasks are hidden, never overdue and not counted; tick Show done in Focus to see them.
- **Untouched** — days since anything changed on the task in Odoo; 7 or more counts as untouched.
- **Local data** — pins, notes, plans, timer and history are stored per account in `%APPDATA%\OdooDesktop\data-*.json`.
- **What Odoo needs** — replies and stage moves need write access to the task; logging time needs the Timesheets app.

## For developers

Needs Python 3.10+ with Tk on Windows. No third-party packages to run.

```powershell
python main.py                                # run from source
python -m unittest discover -s tests -v       # tests
.\build.ps1                                   # tests + rebuild OdooDesktop.exe (needs pyinstaller)
```

| Path | Content |
|---|---|
| `odoo_login/odoo_client.py` | JSON-RPC calls: sign-in, reading tasks, mentions, team, attachments; writing stage, message, timesheet, new task. Sends `User-Agent: OdooDesktop/1.0`: Cloudflare blocks Python's default one. |
| `odoo_login/app.py` | Sign-in screen, stay signed in, tray and window lifecycle |
| `odoo_login/dashboard.py` | Dashboard shell: sidebar, Overview, Focus, What changed, Project health, task page |
| `odoo_login/pages.py` | Today, Calendar, Mentions, Team, Weekly summary, Settings, notifications |
| `odoo_login/actions.py` | Move stage, reply, timer, new task, attachments, form dialogs |
| `odoo_login/insights.py` | Calculations: urgency groups, untouched days, changes, project health, week summary, calendar |
| `odoo_login/richtext.py` | Turns Odoo's HTML (descriptions, messages) into readable text |
| `odoo_login/store.py` | Local data: pins, notes, snapshot, plan, history, timer |
| `odoo_login/winshell.py` | Windows Credential Manager, tray icon and notifications (ctypes, no dependency) |
| `odoo_login/theme.py` | Dark and light palettes, fonts, styles, rounded card and button widgets |
| `odoo_login/settings.py` | Remembered server / database / email / theme / preferences |
| `assets/` | App icon and the Plus Jakarta Sans font (SIL Open Font License, see `assets/fonts/OFL.txt`) |
| `OdooDesktop.spec`, `build.ps1` | Build of the exe |
