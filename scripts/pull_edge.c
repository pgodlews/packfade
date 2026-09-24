/* Read-only Garmin MTP activity importer. No device write/delete APIs. */
#include <libmtp.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <strings.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <errno.h>

static void free_files(LIBMTP_file_t *p) {
    while (p) { LIBMTP_file_t *next = p->next; LIBMTP_destroy_file_t(p); p = next; }
}

static LIBMTP_file_t *listing(LIBMTP_mtpdevice_t *d, uint32_t storage, uint32_t parent) {
    LIBMTP_Clear_Errorstack(d);
    LIBMTP_file_t *p = LIBMTP_Get_Files_And_Folders(d, storage, parent);
    if (LIBMTP_Get_Errorstack(d)) {
        free_files(p);
        fprintf(stderr, "MTP directory read failed. Close other MTP apps and reconnect.\n");
        LIBMTP_Release_Device(d);
        exit(1);
    }
    return p;
}

static uint32_t folder(LIBMTP_mtpdevice_t *d, uint32_t storage, uint32_t parent, const char *name) {
    LIBMTP_file_t *head = listing(d, storage, parent);
    uint32_t found = 0;
    for (LIBMTP_file_t *p = head; p; p = p->next)
        if (p->filename && p->filetype == LIBMTP_FILETYPE_FOLDER && !strcasecmp(p->filename, name)) found = p->item_id;
    free_files(head);
    return found;
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "Usage: pull_edge OUTPUT_DIRECTORY | --list\n"); return 2; }
    int list_only = !strcmp(argv[1], "--list");
    int dirfd = -1;
    if (!list_only) {
        dirfd = open(argv[1], O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
        if (dirfd < 0) { perror("Open output directory"); return 1; }
    }
    LIBMTP_Init();
    LIBMTP_raw_device_t *raw = NULL;
    int count = 0, selected = -1, matches = 0;
    if (LIBMTP_Detect_Raw_Devices(&raw, &count) != LIBMTP_ERROR_NONE) {
        if (raw) free(raw);
        fprintf(stderr, "No MTP device found. Check USB data mode/cable.\n"); return 1;
    }
    for (int i = 0; i < count; ++i)
        if (raw[i].device_entry.vendor_id == 0x091e) { selected = i; ++matches; }
    if (matches != 1) { free(raw); fprintf(stderr, "Connect exactly one Garmin MTP device.\n"); return 1; }
    LIBMTP_mtpdevice_t *d = LIBMTP_Open_Raw_Device_Uncached(&raw[selected]);
    free(raw);
    if (!d) { fprintf(stderr, "Cannot open Garmin. Close Garmin Express or other MTP clients, then reconnect.\n"); return 1; }
    if (LIBMTP_Get_Storage(d, LIBMTP_STORAGE_SORTBY_NOTSORTED) != 0) {
        LIBMTP_Release_Device(d); fprintf(stderr, "Cannot read device storage.\n"); return 1;
    }
    unsigned total = 0, copied = 0, existing = 0, failed = 0;
    uint64_t bytes = 0;
    for (LIBMTP_devicestorage_t *s = d->storage; s; s = s->next) {
        uint32_t garmin = folder(d, s->id, LIBMTP_FILES_AND_FOLDERS_ROOT, "Garmin");
        if (!garmin) continue;
        uint32_t activities = folder(d, s->id, garmin, "Activities");
        if (!activities) continue;
        LIBMTP_file_t *head = listing(d, s->id, activities);
        for (LIBMTP_file_t *p = head; p; p = p->next) {
            const char *name = p->filename;
            if (!name || strlen(name) < 5 || strcasecmp(name + strlen(name) - 4, ".fit") || p->filetype == LIBMTP_FILETYPE_FOLDER) continue;
            ++total; bytes += p->filesize;
            if (list_only) continue;
            /* Use storage + object IDs rather than untrusted device filenames. */
            char dest[80], tmp[96];
            snprintf(dest, sizeof(dest), "%08x-%08x.fit", s->id, p->item_id);
            snprintf(tmp, sizeof(tmp), ".%s.%ld.part", dest, (long)getpid());
            struct stat st;
            if (fstatat(dirfd, dest, &st, AT_SYMLINK_NOFOLLOW) == 0) {
                /* Always download again: object IDs may be reused after reconnect. */
                if (!S_ISREG(st.st_mode)) { ++failed; continue; }
            }
            int fd = openat(dirfd, tmp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
            if (fd < 0) { ++failed; continue; }
            int rc = LIBMTP_Get_File_To_File_Descriptor(d, p->item_id, fd, NULL, NULL);
            int stat_rc = fstat(fd, &st);
            int sync_rc = fsync(fd);
            close(fd);
            if (rc || stat_rc || sync_rc || (uint64_t)st.st_size != p->filesize || st.st_size == 0) {
                unlinkat(dirfd, tmp, 0); ++failed; continue;
            }
            /* Never replace an earlier import; retain new downloads for Python's
               content-addressed archive to deduplicate across MTP object IDs. */
            if (linkat(dirfd, tmp, dirfd, dest, 0) != 0) {
                if (errno == EEXIST) {
                    char alternate[120];
                    snprintf(alternate, sizeof(alternate), "%08x-%08x-%ld.fit", s->id, p->item_id, (long)getpid());
                    if (linkat(dirfd, tmp, dirfd, alternate, 0) != 0) { ++failed; unlinkat(dirfd, tmp, 0); continue; }
                    ++existing;
                } else { ++failed; unlinkat(dirfd, tmp, 0); continue; }
            }
            unlinkat(dirfd, tmp, 0);
            ++copied;
            if (copied % 10 == 0) { printf("Copied %u activities\n", copied); fflush(stdout); }
        }
        free_files(head);
    }
    LIBMTP_Release_Device(d);
    if (dirfd >= 0) close(dirfd);
    printf("Activities: %u; bytes: %llu; copied: %u; repeat IDs retained: %u; failed: %u\n", total, (unsigned long long)bytes, copied, existing, failed);
    if (!total) fprintf(stderr, "No activity FIT files found in Garmin/Activities.\n");
    return failed || !total ? 1 : 0;
}
