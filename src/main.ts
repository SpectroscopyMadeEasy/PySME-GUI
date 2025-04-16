import { app, BrowserWindow } from "electron";
import * as path from "path";
require('@electron/remote/main').initialize()

if (require('electron-squirrel-startup')) app.quit();

// const log = require('electron-log');
// log.transports.file.level = 'debug';
// console.log = log.log;

let mainWindow: Electron.BrowserWindow;

// 由于 Electron 10+ 版本中移除了 allowRendererProcessReuse 属性，此处注释掉相关代码
// app.allowRendererProcessReuse = true

function createWindow() {
  // Create the browser window.
  mainWindow = new BrowserWindow({
    height: 600,
    width: 800,
    minHeight: 300,
    minWidth: 400,
    show: false,
    webPreferences: {
      nodeIntegration: true,
      // 由于 Electron 10+ 版本中移除了 enableRemoteModule 选项，此处应该删除该配置项
      contextIsolation : false,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  require('@electron/remote/main').enable(mainWindow.webContents)

  // and load the index.html of the app.
  mainWindow.loadFile(path.join(__dirname, "../index.html"));

  // Open the DevTools.
  // mainWindow.webContents.openDevTools();
  // mainWindow.maximize()
  mainWindow.on("ready-to-show", () => {
    mainWindow.webContents.openDevTools();
  });
  mainWindow.show()

  // Emitted when the window is closed.
  mainWindow.on("closed", () => {
    // Dereference the window object, usually you would store windows
    // in an array if your app supports multi windows, this is the time
    // when you should delete the corresponding element.
    mainWindow = null;
  });
}

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.on("ready", createWindow);

// Quit when all windows are closed.
app.on("window-all-closed", () => {
  // On OS X it is common for applications and their menu bar
  // to stay active until the user quits explicitly with Cmd + Q
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  // On OS X it"s common to re-create a window in the app when the
  // dock icon is clicked and there are no other windows open.
  if (mainWindow === null) {
    createWindow();
  }
});

// In this file you can include the rest of your app"s specific main process
// code. You can also put them in separate files and require them here.
