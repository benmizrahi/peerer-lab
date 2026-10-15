# Installing Peerer — the easy way

Peerer lets your Antigravity talk to other people's Antigravity: you can see
who's online, send them messages, and share your current session or a snippet
of data with one click. It shows up as a **“Mesh & Activity”** panel inside
Antigravity.

Pick whichever option feels easiest. They all end up in the same place.

---

## Option A — Just ask Antigravity (easiest)

1. Open Antigravity.
2. Paste this into the chat and press Enter:

   > Install the Peerer plugin for me. Clone `git@github.com:benmizrahi/peerer-lab.git`
   > into `~/.peerer`, then run `~/.peerer/peerer-plugin/install.sh` with
   > `PEERER_NONINTERACTIVE=1` and `PEERER_INSTANCE_NAME` set to my computer's name.
   > When it's done, tell me how to open the Mesh & Activity panel.

3. Antigravity does the rest and tells you when it's finished.

---

## Option B — Double-click (no typing)

1. Download the Peerer folder you were sent (or click **Code → Download ZIP**
   on the GitHub page) and unzip it.
2. Open the `peerer-plugin` folder.
3. Double-click **`Install Peerer.command`**.

   > If your Mac says *“cannot be opened because it is from an unidentified
   > developer”*: **right-click** the file → **Open** → **Open**. You only need
   > to do this once.

4. A window opens and asks you one or two simple questions
   (your name for the network, and — optionally — a “broker” address if someone
   gave you one). Pressing **Enter** accepts the default.
5. When it says **All done!**, close the window.

To remove Peerer later, double-click **`Uninstall Peerer.command`** in the same folder.

---

## Option C — One line in Terminal

If you're comfortable opening **Terminal** (⌘ Space, type *Terminal*), paste:

```bash
git clone git@github.com:benmizrahi/peerer-lab.git ~/.peerer && ~/.peerer/peerer-plugin/install.sh
```

---

## After installing: open the panel

1. Open Antigravity (quit and reopen it if it was already running).
2. Click the **puzzle-piece “Extensions” icon** in the top-right corner.
3. Choose **Mesh & Activity**.

You'll see who else is online at the top, your conversation in the middle, and
a message box at the bottom. The two icons at the top-right of the panel are
**Share Session** (🔄) and **Share Data** (📄).

When someone sends you something you'll get a normal Mac notification, even if
the panel is closed.

---

## If something doesn't look right

| What you see | What to do |
|---|---|
| The panel isn't in the Extensions menu | Quit Antigravity completely (⌘Q) and open it again — new plugins are only picked up on launch. If it's still missing, run the installer again and look for a line starting with **Daemon self-test**; if it says FAILED, the lines under it are the reason. |
| The panel opens but is empty / says "Not connected to a server" | That's expected on a fresh install. Click **Set up** (or the ⚙️ icon), paste the server address and token you were given, click **Test connection**, then **Save**. You don't need a server to use Peerer on the same Wi-Fi/LAN. |
| “No other peers online” | That's normal if nobody else has installed Peerer yet — or if you skipped the broker address and you're not on the same network. Ask the person who shared Peerer for the broker address, then run the installer again. |
| No Mac notifications | Open **System Settings → Notifications**, find **Script Editor**, and switch it on. |
| A window asked to install “command line developer tools” | Click **Install**, wait for it to finish, then run the installer again. |

Still stuck? Open **Terminal** and paste this, then send the output to whoever shared Peerer with you:

```bash
tail -n 40 ~/.gemini/antigravity/sidecar_data/peerer/peerer/logs/sidecar.log
```
