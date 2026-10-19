# How to use AI Software Team

Describe a feature in plain English. A small AI team (Product Manager, Developer and Reviewer) writes the spec, builds the code and reviews it. You can also connect a GitHub repository, and the team will add the feature to it and open a pull request for you to review.

**Open the app:** `<your-app-link>`

You don't need to install anything or create any keys.

---

## Option 1: Quick mode (just get code)

Use this when you want code from a description and don't need a repository.

1. **Open the app.** In the left sidebar, make sure **Mode** is set to **Quick (no repo)**.
2. **Write your request** in the *Feature request* box. Be specific. For example:
   *"Create a Python function that checks if a password is strong, with tests"*
3. **(Optional) Set "Max developer attempts"** in the sidebar. This is how many times the Reviewer may send the code back for fixes. 3 is a good default.
4. Click **Run the team**.
5. **Watch the progress.** Cards for each agent light up as they work. Open *Live log* to see every step.
6. **Read the results** in the tabs:

| Tab | What you get |
|---|---|
| **Code** | The generated code, with a download button |
| **Spec** | The Product Manager's written specification |
| **Review** | The Reviewer's feedback and verdict |
| **Tokens & cost** | Tokens and cost for each agent, plus the price used |
| **Run log** | Every step in order |

The coloured banner above the tabs shows the Reviewer's verdict: **approved**, or **stopped at the attempt limit** (the code may still have open review comments, so read the Review tab).

---

## Option 2: GitHub repo mode (add a feature to your repo)

Use this to improve an existing repository, or to build a new project inside an empty one.

### Step 1: Connect GitHub
1. In the sidebar, set **Mode** to **GitHub repo**.
2. Click **Connect GitHub**.
3. GitHub asks you to approve the app. Click **Authorize**.
4. You come back to the app and see *Connected to GitHub as your-username*.

### Step 2: Let the app see your repo (first time only)
The app can only see repositories you have installed it on.

1. If your repo isn't in the **Your repositories** list, click **Install the app on it**.
2. On GitHub, choose your account. Under *Repository access*, pick **Only select repositories** and select the repo.
3. Click **Install**, then return to the app and click **Disconnect** and **Connect GitHub** again so the list refreshes.

*Public repos can be read without connecting, but publishing changes always needs the connection.*

### Step 3: Choose the repo
- Pick it from **Your repositories**, or choose *paste a link instead* and enter `https://github.com/owner/repo`.
- Choose the **Base branch**. Your changes are proposed against this branch.
- If the repo is empty, the app tells you the team will create all the files from your request.

### Step 4: Describe the feature and run
1. Write your request. Mention where it should go if you know. For example:
   *"Add a /health endpoint that returns the app version"*
2. Click **Run the team**.
3. For an existing repo, the **Repo Analyst** goes first. It reads the relevant files and summarises how your project is built. Then the Product Manager, Developer and Reviewer work as usual.

### Step 5: Check the changes
Open the **Changes** tab. Each file is listed with a label:

| Label | Meaning |
|---|---|
| ✏️ modified | An existing file changed. You see a diff (red = removed, green = added). |
| 🆕 new | A new file will be created. |
| ⚠️ overwrites a file the team did not read | The team wants to replace a file it never saw. It is **unticked by default**. Only include it if you are sure. |

Each file has an **Include in the pull request** tick box. Untick anything you don't want.

### Step 6: Publish
1. Edit the **Title** if you like.
2. Click **Create pull request**.
3. When it finishes, click **Open the pull request** to see it on GitHub. Review it carefully, then merge it if you're happy.

**What happens behind the scenes:** the app creates a new branch, commits your ticked files to it, and opens a pull request. Your main branch is never changed directly.

**Empty repositories:** there is no branch to compare against yet, so the button is **Commit to the repository**, and the files are committed straight to the default branch.

---

## Understanding the numbers

After each run you see five cards: **Total cost**, **Input tokens**, **Output tokens**, **LLM calls** and **Time**. The **Tokens & cost** tab breaks them down per agent.

- *Tokens* are the pieces of text the AI reads and writes.
- *Output tokens include "thinking" tokens*, the model's internal reasoning.
- Repo runs use more input tokens than Quick runs, because the team reads your code.
- Costs are estimates based on the model's published price per million tokens.

---

## Tips for better results

- **Be specific.** "Add rate limiting of 5 requests per minute to the login route" works much better than "make it secure".
- **One feature at a time.** Smaller requests give better, easier-to-review changes.
- **Name the technology** if it matters ("use Flask", "plain HTML and CSS").
- **Always review the output.** The AI writes and reviews code, but it never runs or tests it. Run it and test it yourself before you rely on it.

---

## Good to know

- **Your login lasts for the session.** If you refresh or close the page, click **Connect GitHub** again. It takes a few seconds.
- **Large files are skipped.** Files above about 8,000 characters aren't sent to the AI, so the team can't edit very large files.
- **Limited context.** On big repos the team reads only the files it judges most relevant (about 8), so it may miss things elsewhere.
- **Rate limits.** If you see a "rate limit" message, wait a minute and try again, or try a smaller request.
- **Free demo.** The AI model runs on the owner's account, so heavy use may be limited.

## Privacy and permissions

- The app asks for access to **Contents** (to read and write files) and **Pull requests** (to open them) on the repositories **you choose**. It cannot see other repos.
- In repo mode, the files the team reads are sent to the AI provider (Groq) to generate the changes. Don't connect repositories containing secrets or code you aren't allowed to share.
- The app does not store your code or your GitHub token. The token is kept only in your browser session.
- You can remove access at any time: GitHub → Settings → Applications → Installed GitHub Apps → **Configure** → **Uninstall**.

## Troubleshooting

| Problem | What to do |
|---|---|
| My repo isn't in the list | Click **Install the app on it** and select the repo, then reconnect. |
| "Not found or no access" | The app isn't installed on that repo, or the link is wrong. |
| "Button is disabled" under Create pull request | Connect GitHub, tick at least one file, and check the app is installed on the repo. |
| "GitHub says the login expired" | Click **Connect GitHub** again. |
| Rate limit or "request too large" error | Try a smaller request or a smaller repo, and wait a minute. |
| Changes tab says no recognizable files | Run again, or make the request more specific. |
| The result looks wrong | Read the **Review** tab, increase **Max developer attempts**, and rerun with a clearer request. |
