/* Private load-only C ABI. No lua_State or allocator-owned object crosses it. */
#include <stddef.h>
#include <string.h>
#include "lua.h"
#include "lauxlib.h"

#if !defined(_WIN64)
#error This probe is built for the Windows x64 host only.
#endif
typedef char assert_float32[(sizeof(lua_Number) == 4) ? 1 : -1];
typedef char assert_int32[(sizeof(int) == 4) ? 1 : -1];

static void copy_error(char *buffer, size_t capacity, const char *message) {
    size_t length;
    if (!capacity || !buffer) return;
    length = strlen(message ? message : "");
    if (length >= capacity) length = capacity - 1;
    if (length) memcpy(buffer, message, length);
    buffer[length] = '\0';
}

/* path follows the Windows CRT narrow-path convention (ASCII corpus here).
 * Caller owns path and error_buffer. A nonzero capacity requires a writable
 * error_buffer of at least that many bytes. Capacity zero allows NULL.
 * Result: 0 success; positive upstream Lua status; -1 bad arguments; -2 OOM.
 */
__declspec(dllexport) int __cdecl bully_lua50_loadfile(
        const char *path, char *error_buffer, size_t error_capacity) {
    lua_State *L;
    int status;
    if (error_capacity && !error_buffer) return -1;
    copy_error(error_buffer, error_capacity, "");
    if (!path || !path[0]) {
        copy_error(error_buffer, error_capacity, "path is required");
        return -1;
    }
    L = lua_open();
    if (!L) {
        copy_error(error_buffer, error_capacity, "could not allocate Lua state");
        return -2;
    }
    status = luaL_loadfile(L, path);
    if (status) copy_error(error_buffer, error_capacity, lua_tostring(L, -1));
    /* Discard the loaded closure. No call, pcall, resume, or library opening. */
    lua_close(L);
    return status;
}

typedef struct { unsigned char data[18]; size_t length; } ProbeHeader;

static int header_writer(lua_State *L, const void *data, size_t count, void *ud) {
    ProbeHeader *header = (ProbeHeader *)ud;
    size_t take = sizeof(header->data) - header->length;
    (void)L;
    if (count < take) take = count;
    if (take) memcpy(header->data + header->length, data, take);
    header->length += take;
    return 0;
}

/* Return the generated 18-byte chunk header; never execute the probe function.
 * Returns 18 on success, -1 for insufficient output, -2 on VM/probe failure. */
__declspec(dllexport) int __cdecl bully_lua50_chunk_header(
        unsigned char *output, size_t output_capacity) {
    ProbeHeader header;
    lua_State *L;
    if (!output || output_capacity < sizeof(header.data)) return -1;
    memset(&header, 0, sizeof(header));
    L = lua_open();
    if (!L) return -2;
    if (luaL_loadbuffer(L, "return 1", 8, "=dll-header-probe") != 0 ||
            lua_dump(L, header_writer, &header) != 1 || header.length != sizeof(header.data)) {
        lua_close(L);
        return -2;
    }
    memcpy(output, header.data, sizeof(header.data));
    lua_close(L);
    return (int)sizeof(header.data);
}
