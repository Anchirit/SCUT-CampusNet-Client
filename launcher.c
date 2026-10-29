#define _POSIX_C_SOURCE 200809L
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
#ifdef APP_VERSION
    if (argc == 2 && strcmp(argv[1], "--version") == 0) {
        puts("MyDrClient " APP_VERSION);
        return 0;
    }
#endif
#ifdef APP_INSTALL_DIR
    const char *path = APP_INSTALL_DIR;
#else
    char path[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", path, sizeof(path) - 1);
    if (length < 0 || length >= (ssize_t)sizeof(path) - 1) return 1;
    path[length] = '\0';
    char *slash = strrchr(path, '/');
    if (!slash) return 1;
    *slash = '\0';
#endif
    if (chdir(path) != 0) { perror("myDrClient"); return 1; }
    const char *variables[] = {"PYTHONHOME", "PYTHONPATH", "GI_TYPELIB_PATH", "GTK_PATH",
                               "LD_LIBRARY_PATH", "LD_PRELOAD", NULL};
    for (int index = 0; variables[index]; ++index) unsetenv(variables[index]);
    /* This small form needs no GPU renderer. Prefer predictable low-overhead drawing. */
    setenv("GSK_RENDERER", "cairo", 1);
    char **arguments = calloc((size_t)argc + 2, sizeof(char *));
    if (!arguments) return 1;
    arguments[0] = "/usr/bin/python3";
    arguments[1] = "app.py";
    for (int index = 1; index < argc; ++index) arguments[index + 1] = argv[index];
    execv(arguments[0], arguments);
    perror("myDrClient");
    return 1;
}
