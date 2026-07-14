# Vendored third-party binaries

## `orbbec_sdk/`

A workspace-local copy of the **Orbbec SDK** shared libraries
(`libOrbbecSDK.so` + its internal deps), vendored so that camera recording in
this workspace does **not** reach into the `piper_lerobot-main` fork tree for a
runtime binary.

Why this is safe to relocate: the SDK is built with `RPATH=$ORIGIN`, so every
library resolves its siblings (`liblive555.so`, `libob_usb.so`,
`libframe_latency.so`, `libdepthengine.so`) relative to its own directory. The
whole `lib/` directory is therefore self-contained and portable.

### How the path is resolved
`orbbec_sdk_path.py` at the repo root resolves the `.so` path, in order:
1. `$PIPER_ORBBEC_SDK_LIB` (explicit override — file or dir)
2. this vendored copy: `third_party/orbbec_sdk/lib/libOrbbecSDK.so`
3. fork fallback (original hardcoded path) — with a warning

The robot configs (`lerobot_robot_piper_quest3`, `lerobot_robot_bi_piper_quest3`)
call this resolver for every `OrbbecCameraConfig.sdk_lib_path`.

### Restoring after a fresh checkout
The `lib/` contents are git-ignored (≈21 MB of binaries). Re-vendor them with:

```bash
scripts/setup_orbbec_sdk.sh
# or, from a custom source lib dir:
scripts/setup_orbbec_sdk.sh /path/to/OrbbecSDK/install/orbbec_camera/lib
```
