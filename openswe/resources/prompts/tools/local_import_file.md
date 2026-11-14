Copy one explicitly requested local file from the connected desktop into the cloud sandbox, after the desktop owner approves its exact path and size.
`path` is absolute or starts with `~/`; any file type up to 10 MiB.
`sandbox_path` is where it lands, an absolute sandbox path; omit it for `/workspace/local-files/<name>`.
Returns the sandbox path and size, not the contents: read or process the copy with ordinary sandbox tools.
Use it for large, binary, or structured files, and for anything you will analyze or edit; use `local_read_file` for a short text file you only need to read.
Do not import credentials or secrets, and do not seek another route after denial or disconnect.
