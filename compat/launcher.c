#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <X11/Xatom.h>
#include <X11/Xlib.h>

static void message(const char *text) {
    fprintf(stderr, "%s\n", text);
    if (!isatty(STDERR_FILENO) && getenv("DISPLAY")) {
        pid_t child = fork();
        if (child == 0) {
            execlp("zenity", "zenity", "--error", "--title=DrClient", "--text", text, (char *)NULL);
            _exit(1);
        }
    }
}

/* A second double click activates the window belonging to the existing PID. */
static void activate(pid_t pid) {
    Display *display = XOpenDisplay(NULL);
    if (!display) return;
    Window root = DefaultRootWindow(display);
    Atom type, clients = XInternAtom(display, "_NET_CLIENT_LIST", False);
    Atom pid_atom = XInternAtom(display, "_NET_WM_PID", False);
    int format;
    unsigned long count, remaining;
    unsigned char *data = NULL;
    if (XGetWindowProperty(display, root, clients, 0, 4096, False, XA_WINDOW,
                           &type, &format, &count, &remaining, &data) == Success && data) {
        Window *windows = (Window *)data;
        for (unsigned long index = 0; index < count; ++index) {
            unsigned char *value = NULL;
            unsigned long items, left;
            if (XGetWindowProperty(display, windows[index], pid_atom, 0, 1, False,
                                   XA_CARDINAL, &type, &format, &items, &left, &value) == Success && value) {
                if (items == 1 && (pid_t)*(unsigned long *)value == pid) {
                    XEvent event = {0};
                    event.xclient.type = ClientMessage;
                    event.xclient.window = windows[index];
                    event.xclient.message_type = XInternAtom(display, "_NET_ACTIVE_WINDOW", False);
                    event.xclient.format = 32;
                    event.xclient.data.l[0] = 1;
                    event.xclient.data.l[1] = CurrentTime;
                    XMapRaised(display, windows[index]);
                    XSendEvent(display, root, False, SubstructureRedirectMask | SubstructureNotifyMask, &event);
                }
                XFree(value);
            }
        }
        XFree(data);
    }
    XCloseDisplay(display);
}

int main(int argc, char **argv) {
    char directory[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", directory, sizeof(directory) - 1);
    if (length < 0 || length >= (ssize_t)sizeof(directory) - 1) {
        message("无法定位 DrClient 程序目录。"); return 1;
    }
    directory[length] = '\0';
    char *slash = strrchr(directory, '/');
    if (!slash) return 1;
    *slash = '\0';
    if (chdir(directory) != 0) { message("无法进入 DrClient 程序目录。"); return 1; }

    /* Keep ROS, Conda and Qt 5/6 settings out of the bundled Qt 4 process. */
    const char *variables[] = {"LD_LIBRARY_PATH", "LD_PRELOAD", "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM", "QT_IM_MODULE", NULL};
    for (int index = 0; variables[index]; ++index) unsetenv(variables[index]);
    setenv("QT_GRAPHICSSYSTEM", "raster", 1);
    setenv("QT_X11_NO_MITSHM", "1", 1);

    if (argc == 2 && strcmp(argv[1], "--check") == 0) {
        execl("/usr/bin/python3", "python3", "diagnostics/check.py", (char *)NULL);
        message("无法执行自检，请检查 python3。"); return 1;
    }
    struct stat service;
    if (stat("drcomauthsvr", &service) != 0 || service.st_uid != 0 ||
        service.st_gid != 0 || !(service.st_mode & S_ISUID) || access("drcomauthsvr", X_OK) != 0) {
        message("首次使用请先运行同目录的 privillege.sh，完成一次管理员配置后，再双击 DrClientLinux。");
        return 1;
    }
    if (!getenv("DISPLAY")) {
        message("没有可用的 X11 显示。请在 Ubuntu 图形桌面中启动 DrClientLinux。"); return 1;
    }
    int lock = open(".drclient.lock", O_CREAT | O_RDWR, 0600);
    if (lock < 0) { message("程序目录不可写，请将整个 DrClient 目录放到自己的主目录。"); return 1; }
    if (flock(lock, LOCK_EX | LOCK_NB) != 0) {
        char buffer[32] = {0};
        if (read(lock, buffer, sizeof(buffer) - 1) > 0) activate((pid_t)strtol(buffer, NULL, 10));
        close(lock); return 0;
    }
    if (ftruncate(lock, 0) != 0 || dprintf(lock, "%ld\n", (long)getpid()) < 0) {
        message("无法记录客户端进程。"); return 1;
    }
    /* The lock intentionally survives exec and is released when Qt exits. */
    char **arguments = calloc((size_t)argc + 6, sizeof(char *));
    if (!arguments) return 1;
    arguments[0] = "./DrClientLinux.qt4";
    arguments[1] = "-graphicssystem"; arguments[2] = "raster";
    arguments[3] = "-style"; arguments[4] = "cleanlooks";
    for (int index = 1; index < argc; ++index) arguments[index + 4] = argv[index];
    execv(arguments[0], arguments);
    message("无法启动 DrClientLinux.qt4，请检查程序包是否完整。");
    return 1;
}
