# Offline G-code viewer bundle

This small Vue shell loads the upstream G-code Preview component inside the PySide6 Qt WebEngine dialog. The parser and toolpath renderers are third-party packages; this folder only wires the saved, hash-verified artifact to the viewer and adds Calibrate-3DP's local inspection controls.

The distributable JavaScript, CSS, worker files, and third-party notices are built into `src/calibrate3dp/assets/gcode_viewer/` and shipped as package data. The desktop app does not need Node or an Internet connection to preview files.

## Rebuild

- Install Node.js `>=22.12` and npm.
- Run `npm ci` in this directory.
- Run `npm run build` here.

All direct versions are pinned in `package.json` and `package-lock.json`. The bundle contains the upstream parser and renderer, Vue runtime, and Three.js. Vite and its Vue plugin are build-only tools.

## Upstream

- [`@chestnutlabs/gcode-preview-vue` 0.20.1](https://www.npmjs.com/package/@chestnutlabs/gcode-preview-vue), MIT, from Chestnut Labs' [`gcode-preview`](https://github.com/ChestnutLabs/gcode-preview) repository.
- `three` 0.178.0, MIT.
- `vue` 3.5.43, MIT.
- See [`public/THIRD_PARTY_NOTICES.txt`](public/THIRD_PARTY_NOTICES.txt) for bundled notices.
