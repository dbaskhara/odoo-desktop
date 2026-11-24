# Odoo Desktop

A small Windows app to sign in to Odoo and see your projects and the tasks assigned to you.

## Open the app

Double-click **`OdooDesktop.exe`** in this folder. Nothing else to install.

Sign in with:

| Field | What to enter |
|---|---|
| Server | e.g. `your-company.odoo.com` (`https://` is added for you) |
| Database | Your Odoo database name. Optional only if the server lists exactly one database; many servers do not list them, so enter it. |
| Email | your Odoo login |
| Password or API key | Your normal Odoo password, or an API key (Odoo → avatar → My Profile / Preferences → Account Security → New API Key). Accounts with two-step verification must use an API key: Odoo refuses the password for app connections. |

Server, database, email and theme are remembered in `%APPDATA%\OdooDesktop\settings.json` (untick the box to forget them). The password or API key is never saved.

## The dashboard

It loads every open task assigned to you, across all projects, and refreshes every 5 minutes.

The menu on the left switches between four pages. The sun/moon button at the bottom of the menu switches between the dark and light theme; the choice is remembered.

- **Overview** — counters, quick actions and the tasks that need attention now (overdue or due within 7 days). Click a counter or a task to jump to it in Focus.

- **Focus** — one list for all projects, grouped by urgency (Overdue, Today, Next 7 days, Later, No deadline). Search, filter by project, show only pinned or untouched tasks, export the list to CSV. Double-click a task to read it in full.
- **Done tasks** — a task counts as done when Odoo marks it closed, when its stage is folded in the kanban, or when the stage name contains a word like Done or Cancelled. Done tasks are hidden, never shown as overdue and not counted; tick Show done in Focus to see them.
- **Full task page** — double-click a task (or select it and click View full task) to read it inside the app: description, checklists, sub-tasks, messages, assignees, tags, hours and dates. Read-only; Open in Odoo is one click away for editing.
- **Pins and private notes** — select a task to pin it (★, shown first in its group) or write a note (✎). Both stay on this computer and are never sent to Odoo.
- **Untouched** — how many days since anything changed on the task in Odoo; 7 days or more counts as untouched.
- **What changed** — what happened to your tasks since the last time you opened the app: new tasks, stage moves, deadline changes, tasks closed or reassigned.
- **Project health** — one bar per project showing how your tasks split by urgency, with a Late / At risk / On track label. Click a project to see its tasks in Focus.

Pins, notes and the last-visit snapshot are stored per account in `%APPDATA%\OdooDesktop\data-*.json`.

## For developers

Needs Python 3.10+ with Tk. No third-party packages to run.

```powershell
python main.py                                # run from source
python -m unittest discover -s tests -v       # tests
.\build.ps1                                   # tests + rebuild OdooDesktop.exe (needs pyinstaller)
```

| Path | Content |
|---|---|
| `odoo_login/odoo_client.py` | JSON-RPC calls (login, my open tasks, full task details). Sends `User-Agent: OdooDesktop/1.0`: Cloudflare blocks Python's default one. |
| `odoo_login/app.py` | Sign-in screen |
| `odoo_login/dashboard.py` | Dashboard screen (sidebar, Overview, Focus, What changed, Project health) |
| `odoo_login/insights.py` | Calculations: urgency groups, untouched days, changes, project health |
| `odoo_login/richtext.py` | Turns Odoo's HTML (descriptions, messages) into readable text |
| `odoo_login/store.py` | Pins, private notes and last-visit snapshot, saved locally |
| `odoo_login/theme.py` | Dark and light palettes, fonts, styles, rounded card and button widgets |
| `odoo_login/settings.py` | Remembered server / database / email / theme |
| `assets/` | App icon and the Plus Jakarta Sans font (SIL Open Font License, see `assets/fonts/OFL.txt`) |
| `OdooDesktop.spec`, `build.ps1` | Build of the exe |
