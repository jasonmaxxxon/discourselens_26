#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
  (void)argc;
  char exe_path[PATH_MAX];
  if (!realpath(argv[0], exe_path)) {
    perror("realpath");
    return 1;
  }

  char *slash = strrchr(exe_path, '/');
  if (!slash) {
    fprintf(stderr, "invalid launcher path\n");
    return 1;
  }
  *slash = '\0';

  char target[PATH_MAX];
  snprintf(target, sizeof(target), "%s/start.local.sh", exe_path);

  char *const args[] = {"/bin/bash", target, NULL};
  execv("/bin/bash", args);
  perror("execv");
  return 1;
}
