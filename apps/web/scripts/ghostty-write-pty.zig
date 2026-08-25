extern "env" fn openswe_write_pty(terminal: u32, userdata: u32, data: u32, len: u32) void;

export fn ghostty_write_pty(terminal: u32, userdata: u32, data: u32, len: u32) void {
    openswe_write_pty(terminal, userdata, data, len);
}
