# Standalone Mode

This skill runs in standalone mode when the user invokes it directly with a GitHub URL, a local directory of notebooks, or a single notebook file — i.e. when the first message after load does **not** contain the orchestrator context pattern described in `references/orchestrator-mode.md`. Standalone mode targets **Snowpark Connect (SCOS)** only.

## Detecting Standalone Mode

When the skill loads, inspect the first user message.

- **Does** it contain the string `The following context was configured by the spark-migration orchestrator:`? → Orchestrator mode. Load `references/orchestrator-mode.md` and stop reading this file.
- **Does not** contain that string → Standalone mode. Follow this file.

## Prerequisites (standalone-only)

- `git` and `gh` CLI — required for the GitHub repo workflow.
- `cortex artifact` CLI — required only if the user accepts the optional Snowflake Workspace upload step.

These are in addition to the base prerequisites in `SKILL.md` (`uv`, plus the `snowflake-notebooks` skill loaded in the foreground).

## Transformation Rules

All cell-level transformations follow `references/transformation-rules.md`. Setup-cell selection (PySpark vs. non-PySpark) and the File Naming Convention are defined in `SKILL.md` — standalone mode applies them unchanged.

## Workflow Entry Points

Standalone mode has three entry points depending on what the user provided. Dispatch at the top of the workflow:

| Input | Go to |
|-------|-------|
| GitHub URL pointing to a directory of notebooks | [GitHub repo workflow](#github-repo-workflow) |
| Local directory | [Local directory workflow](#local-directory-workflow) |
| Single notebook file | [Single notebook conversion](#single-notebook-conversion) |

## GitHub repo workflow

When the user provides a GitHub URL (e.g. `https://github.com/org/repo/tree/main/path/to/notebooks`):

1. **Clone** the repo locally with `git clone`.
2. **Navigate** to the directory the URL points to.
3. **Ask the user** where to place the converted files — e.g. a sibling folder such as `path/to/notebooks_converted/`, or a custom path they specify.
4. **Create a new branch** for the migration (e.g. `migrate-dbx-notebooks`).
5. **Run the [Local directory workflow](#local-directory-workflow)** against the cloned directory, writing into the destination path chosen in step 3.
6. **Commit** the converted notebooks and any copied `.py` files on the migration branch.
7. **Offer to upload** all converted notebooks to Snowflake Workspace — see [Upload delegation](#upload-delegation). This is complementary to the PR; the user may want both.
8. **Offer to create a pull request** with `gh pr create`.

## Local directory workflow

When the user points to a directory (not a single file), analyze the project before converting anything:

1. **Scan** the directory and its subdirectories using `scripts/scan_dependencies.py` (see `references/tools.md`). Supported input formats are defined by `detect_and_parse_notebook.scan_directory` — see the File Naming Convention table in `SKILL.md` for the authoritative list. Do not scan manually with `find` or `ls`.
2. **Trace dependencies**:
   - `%run` references between notebooks.
   - Python imports that reference local `.py` files found in the directory.
3. **Show the migration plan** to the user before doing any conversion:
   - Dependency graph (list or tree).
   - Recommended conversion order — leaf dependencies first (config, shared notebooks), then the notebooks that `%run` them.
   - List any `.py` files that are imported by the notebooks. These should be uploaded to the same Workspace folder — Python module imports work in Workspaces.
4. **MANDATORY STOPPING POINT** — ask the user: "Would you like to convert all notebooks in the recommended order, or go one by one?" Wait for their response. Do NOT proceed until the user responds.
   - **Convert all**: proceed through them without pausing between notebooks.
   - **One by one**: convert each and ask before proceeding to the next.
5. **Run the [Single notebook conversion](#single-notebook-conversion)** for each notebook in the chosen order.
6. **After all notebooks are converted**:
   - Report how many cells have titles vs. unnamed across all notebooks; offer to name unnamed ones.
   - Run `scripts/validate_directory.py` on the output directory to verify no stale originals remain and all expected `.ipynb` files exist.
   - Offer to upload all converted notebooks to Snowflake Workspace — see [Upload delegation](#upload-delegation).
   - Report a combined migration summary across all notebooks.

If the user points to a single file, skip straight to [Single notebook conversion](#single-notebook-conversion).

## Single notebook conversion

1. **Read** the original DBX notebook to understand its full content and structure. Prefer `scripts/detect_and_parse_notebook.py` for format-agnostic parsing (see `references/tools.md`).
2. **Create the converted notebook** as a new `.ipynb` file in the target location, following the File Naming Convention from `SKILL.md` (e.g. `myfile.python` → `myfile.python.ipynb`). Follow the `snowflake-notebooks` skill for notebook structure (nbformat, SQL cell format, metadata). After writing the converted file, run `validate_notebook.py --finalize <original_path>` to validate the output and delete the original non-`.ipynb` source file. The output folder should only contain `.ipynb` notebooks and non-notebook files.
3. **Process each cell** using the rules in `references/transformation-rules.md`. Copy compatible cells as-is. Apply rules only to incompatible lines. Flag unsupported patterns as markdown migration notes.
4. **Verify `%run` paths** point to the correct post-conversion filenames (rule 7 in the registry).
5. **Carry over cell titles.** If a DBX cell has a title (in `metadata["application/vnd.databricks.v1+cell"]["title"]` or `metadata.title`), set it as `metadata.title` in the converted cell. This title appears in the Snowflake minimap for navigation. After conversion, let the user know how many cells have titles and how many don't, and offer to help name the unnamed ones for better minimap readability.
6. **Add a Migration Summary markdown cell** at the end of the notebook with:
   - Changes made (list every modification with cell reference).
   - Remaining gaps requiring the owner's attention.
   - Count of cells unchanged vs. modified vs. flagged.
7. **Offer to upload** the converted notebook — see [Upload delegation](#upload-delegation).
8. **Report** the same summary to the user.

## Validation feedback loop

After converting each notebook, apply the validation loop defined in `SKILL.md` ("Validation Feedback Loop"). Standalone mode uses the same loop as orchestrator mode — no differences.

## Upload delegation

Snowflake Workspace upload is delegated to the `snowflake-notebooks` skill — standalone mode does not own the `cortex artifact create notebook` command or deeplink generation directly.

To offer upload:

1. Ensure `snowflake-notebooks` is loaded in the foreground (`skill("snowflake-notebooks")`).
2. Follow the `snowflake-notebooks` skill's upload workflow (Step 5) for the `cortex artifact create notebook` command and deeplink URL generation.

This applies equally to single-file, local-directory, and GitHub-repo flows.

## Git / PR workflow (GitHub repo flow only)

After all conversions succeed on the migration branch created in step 4 of the GitHub repo workflow:

1. **Commit** the converted `.ipynb` files and any copied `.py` files.
2. **Push** the branch.
3. **Create a pull request** with `gh pr create`. Use a descriptive title (e.g. `Migrate Databricks notebooks to Snowflake Workspace`) and summarize the combined migration report in the body.

Standalone single-file and local-directory flows do **not** create branches or PRs unless the user explicitly asks — they write converted files in place and stop after the optional upload.

## Stopping Points

- **MANDATORY STOPPING POINT** — Local directory workflow (step 4): after showing the migration plan, ask the user "convert all" or "one by one". Do NOT proceed until the user responds.
- Optional stop after each notebook when the user chose "one by one".
- Optional stop before the upload offer.
- Optional stop before the PR offer (GitHub flow only).

## Troubleshooting

See `SKILL.md` "Troubleshooting". Standalone-specific additions:

- **`git clone` fails** — Report the error and stop. Do not attempt to continue against a partial clone.
- **`gh pr create` fails** — Report the error. The branch is pushed and the converted files are committed, so the user can open a PR manually from the GitHub UI.
- **`cortex artifact create notebook` fails** — Report the error. The converted `.ipynb` file is still valid locally; the user can retry the upload later.
