Scroll the diff the user is looking at to a file, and optionally to a line
inside it, so they are looking at the code you are discussing. Call it as you reference a
location, then keep explaining in your reply. `path` is repository-relative. `line` is a line
number as shown in the diff gutter, and `side` picks which gutter it belongs to: "new" for added
or unchanged lines, "old" for deleted lines. The result is the hunk the user now has on screen,
with both gutters and the target line marked, so read it back before describing what they see.
Only files present in the diff can be shown; for anything else the view is left alone and the
result says so.
