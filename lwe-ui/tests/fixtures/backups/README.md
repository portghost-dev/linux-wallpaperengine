# Backup corpus

Archives `test_backup_corpus.py` restores, one sandbox each. Two kinds live here.

## Generated archives (`corpus-<schema-hash>-<n>.lwebackup`)

Built from the live schemas by

    cd lwe-ui && PYTHONPATH=src python3 tests/corpus/make_corpus.py

Never edited by hand. The generator writes a whole machine's configuration into a temp
sandbox (every settings key at its bounds and at each enum choice, both bool values,
overrides that are sparse, dense, held and referenced, a playlist with an absent member,
tags in every state, favourites, the rule files, a title with a comma and a UTF-8
character), exports it through `storage/backup.py`, and drops the archive here beside a
`.json` of the values it wrote. That JSON is the only expected file the corpus has, and it
is produced by the generator, so a green run cannot be had by editing an expected list.
A settings key holds one value per file, which is why one run writes several archives:
the variants together walk the whole ring of legal values.

The hash in the name is the schema generation the archive was built against. A schema
change means a new hash: regenerate, commit the new archives, and leave the old ones in
place. They must keep restoring. That is the whole point of the corpus, and it is what
`constants.py::RETIRED` and `constants.py::RENAMES` are for when a key legitimately goes
away or changes name.

## Real archives from real builds

Hand-made archives (an export taken off an actual install, older builds included) belong
here too, under any name ending in `.lwebackup`, and are held to the same invariant. Each
needs a `.json` beside it with the same base name, carrying what the machine held when the
archive was taken:

    generated         ISO timestamp; the newest generation is the one held to full
                      schema coverage, so an archive from an older build must not be
                      stamped later than the newest generated corpus
    schema_hash       the build's hash if known, otherwise any stable label
    library_present   the wallpaper ids the restoring library should hold; every other id
                      in the archive exercises the held path
    settings          key -> the typed value the machine held
    theme             key -> value, as the theme store held it
    playlists         slug -> {key: value}
    overrides         wid -> {"kind": "...", "keys": {KEY: raw string}}, exactly the keys
                      the file carried, so set-ness is checked
    tags              rows of {id, title, state}
    meta              wid -> {key: value}
    rules             file name -> the lines it held

## What the receipt lists mean for the invariant

An archive of the current generation was written by the current stores, so a restore of it
must need no accommodation at all, and the suite reads the receipt that way. `dropped` may
only name a key `constants.py::RETIRED` names, or the old name of a renamed key whose new
name the same file carries (a rule LINE is the other exception: a line the file cannot
hold is legal to drop precisely because the receipt says so). `adjusted` must be
empty: a clamp, a snap or a value alias all mean a value this build wrote did not come
back as it went. `preserved` must be empty
too: a key of ours reading as foreign means a store stopped knowing its own key. `notes` may
hold the pre-restore snapshot and nothing else, since a note is how a restore reports a
newer format, a missing settings member or a playlist slug nothing will leave behind. An
archive from an older or newer build is the opposite case and belongs in
`test_backup.py::doors`, which builds one by editing an export in memory and asserts the
restored state: the value under its new name, the clamp with both numbers in the receipt,
the preserved key back in `config/foreign.json` and re-emitted on the next export.

Anything the current build genuinely cannot carry is named in `test_backup_corpus.py`'s
`EXPECTED` table with its reason. An entry there is a finding, not a licence: when the case
starts passing the suite fails until the entry is deleted with the fix. The table is empty.
