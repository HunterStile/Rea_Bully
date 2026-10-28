/* Load-only probe for the original Lua 5.0 VM. No Lua code is executed.
 * The harness does not open standard libraries or register engine natives. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lua.h"
#include "lauxlib.h"
#include "llimits.h"

typedef struct { unsigned char bytes[18]; size_t count; } Header;

static int capture_header(lua_State *L, const void *data, size_t size, void *ud) {
    Header *h = (Header *)ud;
    size_t take = sizeof(h->bytes) - h->count;
    (void)L;
    if (size < take) take = size;
    if (take) memcpy(h->bytes + h->count, data, take);
    h->count += take;
    return 0;
}

static void json_string(const char *value) {
    const unsigned char *p = (const unsigned char *)(value ? value : "");
    putchar('"');
    while (*p) {
        if (*p == '"' || *p == '\\') { putchar('\\'); putchar(*p); }
        else if (*p < 32 || *p > 126) printf("\\u%04x", (unsigned)*p);
        else putchar(*p);
        ++p;
    }
    putchar('"');
}

static int print_config(void) {
    Header header;
    lua_State *L = lua_open();
    size_t i;
    memset(&header, 0, sizeof(header));
    if (!L) return 1;
    /* Compile a trivial source function, then dump it. Never invoke it. */
    if (luaL_loadbuffer(L, "return 1", 8, "=header-probe") != 0) {
        fprintf(stderr, "header probe compile failed: %s\n", lua_tostring(L, -1));
        lua_close(L); return 1;
    }
    /* Lua 5.0 returns 1 on dump success (later Lua releases changed this). */
    if (lua_dump(L, capture_header, &header) != 1) {
        fprintf(stderr, "header probe dump failed: stack type %s\n", lua_typename(L, lua_type(L, -1)));
        lua_close(L); return 1;
    }
    printf("{\"lua_version\":"); json_string(LUA_VERSION);
    printf(",\"pointer_bytes\":%u,\"host_size_t_bytes\":%u,\"int_bytes\":%u,"
           "\"instruction_bytes\":%u,\"number_bytes\":%u,\"memory_counter_bytes\":%u,"
           "\"generated_chunk_header_hex\":\"",
           (unsigned)sizeof(void *), (unsigned)sizeof(size_t), (unsigned)sizeof(int),
           (unsigned)sizeof(Instruction), (unsigned)sizeof(lua_Number), (unsigned)sizeof(lu_mem));
    for (i = 0; i < header.count; ++i) printf("%02x", (unsigned)header.bytes[i]);
    printf("\",\"game_chunk_execution\":false}");
    lua_close(L);
    return 0;
}

static int validate_file(const char *path) {
    lua_State *L = lua_open();
    int status;
    if (!L) return -1;
    status = luaL_loadfile(L, path);
    printf("{\"path\":"); json_string(path);
    printf(",\"load_status\":%d,\"loaded_function\":%s,\"error\":", status,
           status == 0 && lua_isfunction(L, -1) ? "true" : "false");
    if (status) json_string(lua_tostring(L, -1)); else printf("null");
    printf("}");
    /* Pop/discard the closure by closing its state. No call/pcall/resume. */
    lua_close(L);
    return status;
}

int main(int argc, char **argv) {
    FILE *list;
    char path[4096];
    int first = 1, count = 0, failures = 0;
    if (argc == 2 && strcmp(argv[1], "--info") == 0) {
        int status = print_config(); putchar('\n'); return status;
    }
    if (argc != 3 || strcmp(argv[1], "--filelist") != 0) {
        fprintf(stderr, "usage: lua50_validate --info | --filelist paths.txt\n");
        return 2;
    }
    list = fopen(argv[2], "rb");
    if (!list) { perror(argv[2]); return 2; }
    printf("{\"config\":");
    if (print_config()) { fclose(list); return 2; }
    printf(",\"files\":[");
    while (fgets(path, sizeof(path), list)) {
        size_t len = strlen(path);
        if (len && path[len - 1] != '\n' && !feof(list)) {
            fclose(list); fprintf(stderr, "input path exceeds harness buffer\n"); return 2;
        }
        while (len && (path[len - 1] == '\r' || path[len - 1] == '\n')) path[--len] = 0;
        if (!len) continue;
        if (!first) putchar(',');
        first = 0;
        if (validate_file(path) != 0) ++failures;
        ++count;
    }
    fclose(list);
    printf("],\"file_count\":%d,\"load_failures\":%d,\"game_chunk_execution\":false}\n", count, failures);
    return failures ? 1 : 0;
}
