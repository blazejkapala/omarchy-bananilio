import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Countries.js" as Countries
import "Segments.js" as Segments

Panel {
  id: root
  moduleName: "banan.bananilio"
  ipcTarget: moduleName
  manageIpc: false
  IpcHandler {
    target: "banan.bananilio"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function inbox(): void { root.page = "Inbox"; root.open() }
    function settings(): void { root.page = "Settings"; root.open() }
    function contacts(): void { root.page = "Contacts"; root.open() }
    function demoGeometry(): string {
      if (!root.demoMode || !root.opened || !root.loaded) return "{}"
      return JSON.stringify({x: popup.cardOrigin.x, y: popup.cardOrigin.y, width: popup.contentWidth, height: popup.contentHeight})
    }
    function compose(): void { root.page = "Compose"; root.open() }
    function lock(): void { root.lockNow() }
    function status(): string {
      return JSON.stringify({locked: root.locked, incognito: root.incognitoEnabled, ready: root.securityReady, demo: root.demoMode, loaded: root.loaded, accounts: root.accountList.length,
        numbers: root.account.numbers.length, messages: root.account.messages.length,
        accountError: !!root.account.error, busy: poll.running || action.running})
    }
    function demoCompose(): void {
      if (!root.demoMode || root.locked) return
      demoPreview(); root.preview = null
    }
    function demoPreview(): void {
      if (root.locked || !root.demoMode || action.running || !root.selectedNumber) return
      root.resetDraft()
      root.countryCode = "US"
      recipient.text = "+12025550120"
      body.text = "Demo: hello from Bananilio! This is a fictional message."
      root.preview = {action: "send", account: root.selectedAccount, from: root.selectedNumber,
        to: recipient.text, body: body.text, request_id: "demo-preview-" + Date.now().toString()}
      root.page = "Compose"; root.open()
    }
  }
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property int barSize: bar ? bar.barSize : Style.bar.sizeHorizontal
  readonly property bool demoMode: setting("demo", false) === true
  readonly property string scriptPath: Qt.resolvedUrl("client.py").toString().replace(/^file:\/\//, "")
  property var accountList: []
  property string selectedAccount: ""
  property string selectedNumber: ""
  property int unread: 0
  property string page: "Inbox"
  property string status: ""
  property bool loaded: false
  property var preview: null
  property bool attempted: false
  property bool showPolishPreview: false
  readonly property var segmentEstimate: Segments.estimate(body.text, selectedNumber, recipientNumber)
  readonly property string polishPreview: showPolishPreview ? Segments.withoutPolish(body.text) : ""
  property bool accountSetupOpen: false
  property string disconnectCandidate: ""
  readonly property string defaultCountry: Countries.get(String(setting("defaultCountry", "PL"))).value
  property string countryCode: defaultCountry
  readonly property string recipientNumber: Countries.normalize(recipient.text)
  property bool importPending: false
  property string editingContact: ""
  property string deletingContact: ""
  property bool contactEditorOpen: false
  property var demoBooks: ({})
  property var demoShared: ({})
  property var demoLabels: ({})
  readonly property bool contactsShared: demoMode ? !!demoShared[selectedAccount] : !!account.contacts_shared
  readonly property var contacts: {
    if (!selectedNumber) return []
    if (demoMode) {
      var key = selectedAccount + ":" + (contactsShared ? "shared" : selectedNumber)
      return demoBooks[key] || [{name: "Demo Alex", number: "+12025550120"}, {name: "Demo Casey", number: "+12025550121"}]
    }
    return (account.contact_books || {})[selectedNumber] || []
  }
  readonly property var contactOptions: contacts.filter(function(c) { return /^\+[1-9][0-9]{6,14}$/.test(c.number) }).map(function(c) { return {value: c.number, label: c.name + " · " + c.number} })
  function labelFor(aid, n) { return demoMode ? demoLabels[aid + ":" + n.number] || "" : n.label || "" }
  function numberTitle(aid, n) { var label = labelFor(aid, n); return n.number + (label ? " (" + label + ")" : "") }
  function contactName(number) { var c = contacts.find(function(c) { return c.number === number }); return c ? c.name + " · " + number : number }
  function chooseCountry(code) {
    activity()
    var old = Countries.get(countryCode).dial, value = recipientNumber
    countryCode = code
    recipient.text = Countries.get(code).dial + (value.indexOf(old) === 0 ? value.slice(old.length) : "")
  }
  function chooseContact(number) { recipient.text = number; countryCode = Countries.detect(number, countryCode); page = "Compose" }
  function clearContactEditor() { editingContact = ""; deletingContact = ""; contactEditorOpen = false; contactNameField.text = ""; contactNumberField.text = ""; contactSearch.text = "" }
  function demoContactAction(data) {
    var key = selectedAccount + ":" + (contactsShared ? "shared" : selectedNumber)
    var book = contacts.slice(), books = Object.assign({}, demoBooks)
    if (data.action === "share_contacts") {
      var sharedKey = selectedAccount + ":shared"
      if (data.shared && !books[sharedKey]) books[sharedKey] = book.slice()
      var sharing = Object.assign({}, demoShared); sharing[selectedAccount] = data.shared; demoShared = sharing
    } else if (data.action === "set_number_label") {
      var labels = Object.assign({}, demoLabels); labels[data.account + ":" + data.number] = data.label; demoLabels = labels
    } else if (data.action === "delete_contact") {
      books[key] = book.filter(function(c) { return c.number !== data.number })
    } else {
      if (book.some(function(c) { return c.number === data.number && c.number !== data.original })) { status = "This number already exists. Edit that contact instead."; return }
      book = book.filter(function(c) { return c.number !== data.original })
      book.push({name: data.name, number: data.number}); books[key] = book
    }
    demoBooks = books; clearContactEditor(); status = "Demo changes saved for this session."
  }
  onDefaultCountryChanged: {
    if (!recipient.text || recipient.text === Countries.get(countryCode).dial) {
      countryCode = defaultCountry; recipient.text = Countries.get(countryCode).dial
    }
  }
  property bool encryptedStorage: false
  property bool storageSetup: false
  property bool legacyLock: false
  property bool securityReady: false
  property bool incognitoEnabled: false
  property bool locked: true
  property int idleSeconds: 60
  property string sessionToken: ""
  property string securityMessage: ""
  property double lastActivity: Date.now()
  property double lastTouch: 0
  property int generation: 0
  property var drafts: ({})
  function checkIdle() {
    if (incognitoEnabled && !locked && Date.now() - lastActivity >= idleSeconds * 1000) lockNow()
  }
  function activity() {
    checkIdle()
    if (!securityReady || locked || !opened) return
    lastActivity = Date.now()
    if (incognitoEnabled && !security.running && Date.now() - lastTouch >= 1000) {
      lastTouch = Date.now(); securityRequest({action: "touch_session", session: sessionToken})
    }
  }
  function lockNow() {
    if (!incognitoEnabled && securityReady && !encryptedStorage) return
    generation++; locked = true; sessionToken = ""
    stopBackend()
    accountList = []; selectedAccount = ""; selectedNumber = ""; unread = 0; loaded = false
    status = ""; page = "Inbox"; drafts = ({}); resetDraft(); clearCredentials()
    setupPassword.text = ""; setupConfirm.text = ""; unlockPassword.text = ""; newPassword.text = ""; confirmPassword.text = ""; currentPassword.text = ""
    securityMessage = ""; importPending = false; clearContactEditor(); countryPicker.close(); defaultCountryPicker.close(); contactPicker.close()
  }
  function initializeSecurity() {
    if (demoMode) {
      incognitoEnabled = false; locked = false; securityReady = true; refresh(false)
    } else securityRequest({action: "security_status"})
  }
  function securityRequest(data) {
    if (security.running || demoMode) return
    security.kind = data.action; security.epoch = generation
    submit(security, data)
    if (data.action === "unlock" || data.action === "create_vault" || data.action === "configure_security") {
      unlockPassword.text = ""; setupPassword.text = ""; setupConfirm.text = ""; currentPassword.text = ""; newPassword.text = ""; confirmPassword.text = ""
    }
  }
  QtObject {
    id: security
    property bool running: false
    property string kind: ""
    property int epoch: 0
    function finish(result) {
      running = false
      if (epoch !== root.generation) { if (!root.securityReady) Qt.callLater(root.initializeSecurity); return }
      if (!result.ok) {
        if (result.locked) root.lockNow()
        root.securityMessage = result.error
        return
      }
      if (kind === "touch_session") return
      root.encryptedStorage = !!result.encrypted
      root.storageSetup = !!result.setup
      root.legacyLock = !!result.legacy_lock
      if (root.storageSetup) {
        root.accountList = []; root.selectedAccount = ""; root.selectedNumber = ""; root.unread = 0; root.loaded = false
        root.drafts = ({}); root.resetDraft(); root.clearContactEditor(); root.clearCredentials()
      }
      root.incognitoEnabled = !!result.enabled
      root.idleSeconds = result.timeout || 60; timeoutField.text = String(root.idleSeconds)
      root.securityReady = true
      root.sessionToken = result.session || ""
      root.locked = root.storageSetup || (result.enabled && !result.session)
      root.lastActivity = Date.now(); root.lastTouch = Date.now()
      unlockPassword.text = ""; currentPassword.text = ""; newPassword.text = ""; confirmPassword.text = ""
      root.securityMessage = kind === "configure_security" ? "Security settings saved." : ""
      if (!root.locked) Qt.callLater(function() { root.refresh(false) })
    }
  }
  Timer { interval: 250; repeat: true; running: root.incognitoEnabled && !root.locked; onTriggered: root.checkIdle() }

  property var demoVisibility: ({})
  readonly property var visibleAccounts: accountList.filter(function(a) { return root.numbersFor(a).length > 0 })
  readonly property var visibleNumbers: numbersFor(account)
  function numbersFor(a) {
    return a.numbers.filter(function(n) {
      var chosen = root.demoVisibility[a.id]
      return root.demoMode ? chosen === undefined || chosen.indexOf(n.number) >= 0 : n.visible !== false
    })
  }
  function selectContext(aid, number) {
    if (action.running || preview) return
    var key = selectedAccount + ":" + selectedNumber
    drafts[key] = {to: recipient.text, body: body.text, country: countryCode}
    selectedAccount = aid; selectedNumber = number
    var next = drafts[aid + ":" + number] || {to: Countries.get(defaultCountry).dial, body: "", country: defaultCountry}
    showPolishPreview = false; recipient.text = next.to; body.text = next.body; countryCode = next.country || defaultCountry; clearContactEditor()
    status = ""
  }
  function normalizeSelection() {
    var next = visibleAccounts.find(function(a) { return a.id === root.selectedAccount })
    if (!next) next = visibleAccounts.length ? visibleAccounts[0] : null
    var available = next ? numbersFor(next) : []
    var number = available.some(function(n) { return n.number === root.selectedNumber }) ? selectedNumber : available.length ? available[0].number : ""
    var aid = next ? next.id : ""
    if (aid !== selectedAccount || number !== selectedNumber) {
      preview = null; attempted = false
      selectContext(aid, number)
    }
  }
  function toggleNumber(a, number) {
    var selected = numbersFor(a).map(function(n) { return n.number })
    var index = selected.indexOf(number)
    if (index >= 0) selected.splice(index, 1); else selected.push(number)
    if (demoMode) {
      var next = Object.assign({}, demoVisibility); next[a.id] = selected; demoVisibility = next
      normalizeSelection(); status = "Demo visibility updated."
    } else run({action: "set_visible_numbers", account: a.id, numbers: selected})
  }
  readonly property var account: accountList.find(function(a) { return a.id === selectedAccount }) || ({numbers: [], messages: [], error: ""})
  readonly property var messages: account.messages.filter(function(m) {
    return m.inbound === (root.page === "Inbox") && (m.inbound ? m.to : m.from) === root.selectedNumber
  })
  implicitWidth: barSize
  implicitHeight: barSize

  property int requestCounter: 0
  property var pendingRequests: ({})
  property var queuedRequests: []
  function stopBackend() {
    if (backend.running) backend.signal(9)
    queuedRequests = []; pendingRequests = ({})
    security.running = false; poll.running = false; action.running = false
  }
  function submit(role, data) {
    var id = String(++requestCounter)
    data.id = id; role.running = true
    pendingRequests[id] = {role: role, epoch: generation}
    var line = JSON.stringify(data) + "\n"
    if (backend.running && backend.ready) backend.write(line)
    else {
      queuedRequests.push(line)
      if (!backend.running) { backend.command = command(); backend.running = true }
    }
  }
  Process {
    id: backend
    property bool ready: false
    stdinEnabled: true
    onStarted: {
      ready = true
      var queued = root.queuedRequests; root.queuedRequests = []
      for (var i = 0; i < queued.length; ++i) write(queued[i])
    }
    stdout: SplitParser {
      onRead: function(line) {
        var result = root.parse(line)
        if (result.event === "locked") { root.lockNow(); return }
        var pending = root.pendingRequests[result.id]
        delete root.pendingRequests[result.id]
        if (pending && pending.epoch === root.generation) pending.role.finish(result)
      }
    }
    onExited: {
      ready = false
      var unexpected = security.running || poll.running || action.running || (root.encryptedStorage && !root.locked)
      if (unexpected) {
        root.lockNow()
        root.securityMessage = "Connection to the local helper closed. Unlock again. If sending was interrupted, check Sent before trying again."
      }
      security.running = false; poll.running = false; action.running = false
    }
  }

  function command() {
    var cmd = ["/usr/bin/python3", "-I", scriptPath, "--serve"]
    if (demoMode) cmd.push("--demo")
    return cmd
  }
  function parse(text) {
    try { return JSON.parse(text) } catch(e) { return {ok: false, error: "No valid response. Check your connection and configuration."} }
  }
  function refresh(fresh) {
    if (!securityReady || locked || poll.running || action.running) return
    poll.requestDemo = demoMode; poll.epoch = generation
    submit(poll, {action: "snapshot", fresh_numbers: !!fresh, limit: 200, session: sessionToken})
  }
  function run(data) {
    activity()
    if (!securityReady || locked || action.running) return
    if (demoMode && ["save_contact", "delete_contact", "share_contacts", "set_number_label"].indexOf(data.action) >= 0) { demoContactAction(data); return }
    data.session = sessionToken
    action.epoch = generation
    action.kind = data.action
    action.requestDemo = demoMode
    submit(action, data)
    status = "Working…"
  }
  function setOption(key, value) {
    Quickshell.execDetached(["/usr/share/omarchy/bin/omarchy", "bar", "set", moduleName, key, JSON.stringify(value), "--json"])
  }
  function resetDraft() {
    showPolishPreview = false
    delete drafts[selectedAccount + ":" + selectedNumber]
    preview = null; attempted = false; countryCode = defaultCountry; recipient.text = Countries.get(defaultCountry).dial; body.text = ""
  }
  function clearCredentials() { accountSetupOpen = false; disconnectCandidate = ""; accountName.text = ""; sid.text = ""; token.text = "" }
  onPageChanged: if (page !== "Settings") clearCredentials()
  onOpenedChanged: {
    if (opened) { checkIdle(); refresh(false) }
    else { clearCredentials(); unlockPassword.text = ""; currentPassword.text = ""; newPassword.text = ""; confirmPassword.text = "" }
  }
  onDemoModeChanged: {
    stopBackend(); storageSetup = false; encryptedStorage = false;
    generation++; securityReady = false; locked = true; sessionToken = ""
    accountList = []; selectedAccount = ""; selectedNumber = ""; unread = 0; loaded = false
    status = ""; drafts = ({}); demoVisibility = ({}); demoBooks = ({}); demoShared = ({}); demoLabels = ({}); clearContactEditor(); resetDraft(); clearCredentials(); Qt.callLater(root.initializeSecurity)
  }
  Component.onCompleted: initializeSecurity()

  QtObject {
    id: poll
    property bool running: false
    property bool requestDemo: false
    property int epoch: 0
    function finish(result) {
      running = false
      if (epoch !== root.generation || root.locked) return
      if (requestDemo !== root.demoMode) { Qt.callLater(function() { root.refresh(false) }); return }
      if (!result.ok) { if (result.locked) root.lockNow(); else root.status = result.error; return }
      root.accountList = result.accounts || []; root.unread = result.unread || 0; root.loaded = true; root.importPending = !!result.contact_import_pending
      root.normalizeSelection()
      if (!requestDemo && result.new_count > 0 && root.setting("notifyNewMessages", true))
        Quickshell.execDetached(["notify-send", "Bananilio", String(result.new_count) + " new SMS. Open Bananilio to read."])
    }
  }
  QtObject {
    id: action
    property bool running: false
    property string kind: ""
    property bool requestDemo: false
    property int epoch: 0
    function finish(result) {
      running = false
      if (epoch !== root.generation || root.locked) return
      if (requestDemo !== root.demoMode) { Qt.callLater(function() { root.refresh(false) }); return }
      if (!result.ok) {
        if (result.locked) { root.lockNow(); return }
        root.status = result.error + (kind === "send" ? " Check Sent before starting another message." : "")
        return
      }
      if (kind === "send") {
        root.status = requestDemo ? "Demo send simulated. No SMS was sent." : "Twilio accepted the message. Status: " + result.status + "."
        root.resetDraft(); root.page = "Sent"
      } else if (kind === "add_account") {
        root.clearCredentials(); root.status = requestDemo ? "Demo account validation simulated." : "Account saved. SMS numbers: " + result.number_count
      } else if (kind === "disconnect_account") {
        root.accountList = []; root.drafts = ({}); root.resetDraft(); root.selectedAccount = ""; root.selectedNumber = ""
        root.disconnectCandidate = ""; root.status = "Account disconnected. Twilio messages were not deleted."
      } else if (kind === "set_visible_numbers") root.status = "Visible numbers saved."
      else if (kind === "save_contact" || kind === "delete_contact" || kind === "share_contacts") { root.clearContactEditor(); root.status = "Address book updated." }
      else if (kind === "set_number_label") root.status = "Number label saved."
      else if (kind === "import_contacts") root.status = "Imported " + result.imported_contacts + " contacts into " + result.books + " separate books."
      else root.status = "Marked as read."
      Qt.callLater(function() { root.refresh(false) })
    }
  }
  Timer {
    interval: Math.max(15, Math.min(600, Number(root.setting("refreshIntervalSec", 30)))) * 1000
    repeat: true; running: true
    onTriggered: root.refresh(false)
  }

  BarIconButton {
    id: barButton
    anchors.fill: parent
    bar: root.bar
    text: ""
    foreground: root.foreground
    iconComponent: Component { BananilioIcon { color: root.foreground } }
    tooltipText: root.locked ? "Bananilio · Locked" : "Bananilio" + (root.demoMode ? " · DEMO" : "") + " · " + root.unread + " unread"
    onPressed: function(button) { if (button === Qt.LeftButton) root.toggle() }
    Rectangle {
      visible: !root.locked && root.unread > 0; width: 5; height: 5; radius: 3
      anchors.right: parent.right; anchors.top: parent.top
      color: root.foreground
    }
  }

  component Label: Text {
    color: root.foreground
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
  }
  component ActionButton: Button { focusable: true; foreground: root.foreground; onClicked: root.activity() }
  component PrivateField: TextField { onTextEdited: root.activity() }

  KeyboardPanel {
    id: popup
    anchorItem: barButton; owner: root; bar: root.bar; open: root.opened
    contentWidth: fittedContentWidth(Style.space(540))
    contentHeight: cappedContentHeight(Style.space(root.locked ? (root.storageSetup ? 680 : 300) : root.page === "Settings" ? 900 : root.page === "Compose" ? 900 : 700))
    focusTarget: content
    Item {
      id: content
      anchors.fill: parent
      Keys.onEscapePressed: root.close()
      TapHandler { onPressedChanged: if (pressed) root.activity() }
      WheelHandler { target: null; blocking: false; onWheel: function(event) { root.activity(); event.accepted = false } }
      Controls.ScrollView {
        id: mainScroll
        anchors.fill: parent; clip: true
        contentWidth: availableWidth
        ColumnLayout {
          width: parent.width
          spacing: Style.space(12)
          PanelHero {
            Layout.fillWidth: true
            title: "Bananilio"
            meta: root.locked ? "Incognito locked" : "Twilio SMS"
            detail: root.demoMode ? "DEMO" : ""
            iconComponent: Component { BananilioIcon { iconSize: Style.space(28); color: root.foreground } }
            trailingControl: Component {
              PanelActionButton {
                iconText: "⚙"; tooltipText: "Settings"; foreground: root.foreground
                enabled: root.securityReady && !root.locked
                onClicked: root.page = root.page === "Settings" ? "Inbox" : "Settings"
              }
            }
          }
          ColumnLayout {
            Layout.fillWidth: true; visible: root.locked || !root.securityReady
            Label { Layout.fillWidth: true; text: root.storageSetup ? "Choose how Bananilio stores your local data." : root.securityReady ? "Enter your Bananilio password to view messages." : "Checking local storage…" }
            PrivateField {
              id: unlockPassword; Layout.fillWidth: true; password: true; visible: !root.storageSetup || root.legacyLock; placeholderText: root.storageSetup ? "Current Incognito password" : "Password"; maximumLength: 128
              enabled: root.securityReady && !security.running
              onAccepted: if (!root.storageSetup && text.length && !security.running) root.securityRequest({action: "unlock", password: text})
            }
            ActionButton { visible: !root.storageSetup; text: "Unlock"; enabled: root.securityReady && !security.running && unlockPassword.text.length > 0; onClicked: root.securityRequest({action: "unlock", password: unlockPassword.text}) }
            ColumnLayout {
              Layout.fillWidth: true; visible: root.storageSetup
              Label { Layout.fillWidth: true; text: "Encrypted storage protects accounts, contacts and local state with one master password. Locking closes the vault. There is no password recovery." }
              PrivateField { id: setupPassword; Layout.fillWidth: true; password: true; placeholderText: "Master password · at least 8 characters"; maximumLength: 128 }
              PrivateField { id: setupConfirm; Layout.fillWidth: true; password: true; placeholderText: "Confirm master password"; maximumLength: 128 }
              Label { Layout.fillWidth: true; text: "Existing local data is verified in the encrypted database before its old unencrypted files are removed. External backups are unchanged." }
              ActionButton {
                text: "Use encrypted storage"; enabled: !security.running && setupPassword.text.length >= 8 && setupPassword.text === setupConfirm.text && (!root.legacyLock || unlockPassword.text.length > 0)
                onClicked: root.securityRequest({action: "create_vault", password: setupPassword.text, current_password: unlockPassword.text})
              }
              Label { Layout.fillWidth: true; text: "Standard storage keeps local files unencrypted. Optional Incognito only hides the panel." }
              ActionButton { text: "Use standard storage"; enabled: !security.running; onClicked: root.securityRequest({action: "use_standard"}) }
            }
            Label { Layout.fillWidth: true; text: root.securityMessage; visible: text !== "" }
            ActionButton { text: "Retry"; visible: !root.securityReady; enabled: !security.running; onClicked: root.initializeSecurity() }
          }
          ColumnLayout {
            Layout.fillWidth: true; spacing: Style.space(10)
            visible: root.securityReady && !root.locked
          Label {
            Layout.fillWidth: true; visible: root.demoMode
            text: "Demo mode · Fictional data. No account access or real SMS sending."
          }
          ColumnLayout {
            Layout.fillWidth: true; visible: root.loaded && !root.accountList.length && root.page !== "Settings"
            Label { text: "Welcome to Bananilio"; font.bold: true }
            Label { Layout.fillWidth: true; text: "Connect your Twilio account to read and send SMS. You will need its Account SID, Auth Token and an SMS-enabled Twilio phone number." }
            ActionButton { text: "Connect Twilio account"; onClicked: { root.page = "Settings"; root.accountSetupOpen = true } }
          }
          ColumnLayout {
            Layout.fillWidth: true; spacing: Style.space(10)
            visible: root.page !== "Settings"
          Flow {
            Layout.fillWidth: true; spacing: 6
            enabled: !root.preview && !action.running
            Repeater {
              model: root.visibleAccounts.length > 1 ? root.visibleAccounts : []
              ActionButton {
                required property var modelData
                text: modelData.name; selected: root.selectedAccount === modelData.id
                onClicked: root.selectContext(modelData.id, root.numbersFor(modelData)[0].number)
              }
            }
          }
          Flow {
            Layout.fillWidth: true; spacing: 6
            enabled: !root.preview && !action.running
            Repeater {
              model: root.visibleNumbers
              ActionButton {
                required property var modelData
                text: root.numberTitle(root.selectedAccount, modelData); selected: root.selectedNumber === modelData.number
                onClicked: root.selectContext(root.selectedAccount, modelData.number)
              }
            }
          }
          Label {
            Layout.fillWidth: true
            visible: root.loaded && !root.visibleAccounts.length && root.accountList.length > 0
            text: "All numbers are hidden. Open the gear → Visible numbers to choose one."
          }
          Rectangle { Layout.fillWidth: true; height: 1; color: root.foreground; opacity: 0.15 }
          Flow {
            Layout.fillWidth: true; spacing: 6
            Repeater {
              model: root.accountList.length ? ["Inbox", "Sent", "Contacts", "New message"] : []
              ActionButton {
                required property string modelData
                readonly property string targetPage: modelData === "New message" ? "Compose" : modelData
                text: modelData; selected: root.page === targetPage
                onClicked: root.page = targetPage
              }
            }
          }
          }
          Label { Layout.fillWidth: true; visible: root.status !== ""; text: root.status }
          Label { Layout.fillWidth: true; visible: !!root.account.error; text: "Could not refresh this account: " + root.account.error }
          Label {
            Layout.fillWidth: true
            visible: root.page !== "Settings" && !root.accountList.length
            text: root.loaded ? "" : "Loading accounts…"
          }

          ColumnLayout {
            Layout.fillWidth: true
            visible: root.accountList.length > 0 && (root.page === "Inbox" || root.page === "Sent")
            spacing: 10
            RowLayout {
              Layout.fillWidth: true
              Label { Layout.fillWidth: true; text: root.messages.length + " messages · latest 200 per account" }
              ActionButton { text: poll.running ? "Refreshing…" : "Refresh"; enabled: !poll.running && !action.running; onClicked: root.refresh(false) }
            }
            Label { Layout.fillWidth: true; visible: root.loaded && !root.messages.length && !root.account.error; text: "No messages for this number in the loaded history." }
            Repeater {
              model: root.messages
              Rectangle {
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: messageColumn.implicitHeight + 24
                radius: 8
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.05)
                border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.15)
                ColumnLayout {
                  id: messageColumn
                  anchors.fill: parent; anchors.margins: 12
                  Label { Layout.fillWidth: true; font.bold: true; text: (modelData.unread ? "●  " : "") + (modelData.inbound ? "From " + root.contactName(modelData.from) : "To " + root.contactName(modelData.to)) }
                  Label { Layout.fillWidth: true; text: modelData.body }
                  Label { Layout.fillWidth: true; opacity: 0.65; text: modelData.date + " · " + modelData.status + (Number(modelData.segments) > 0 ? " · " + modelData.segments + " segments (Twilio)" : "") + (modelData.error_code ? " · Error " + modelData.error_code : "") + (modelData.media ? " · " + modelData.media + " media attachments (text only)" : "") }
                  RowLayout {
                    ActionButton {
                      text: "Reply"; visible: modelData.inbound; enabled: !action.running && !root.preview
                      onClicked: { root.resetDraft(); root.chooseContact(modelData.from) }
                    }
                    ActionButton {
                      text: "Mark read"; visible: modelData.unread; enabled: !action.running
                      onClicked: root.run({action: "mark_read", keys: [modelData.key]})
                    }
                  }
                }
              }
            }
          }

          ColumnLayout {
            Layout.fillWidth: true; visible: root.page === "Compose"; spacing: 10
            Label { Layout.fillWidth: true; text: "From: " + (root.selectedNumber ? root.numberTitle(root.selectedAccount, root.visibleNumbers.find(function(n) { return n.number === root.selectedNumber }) || {number: root.selectedNumber}) : "Select an SMS-enabled number") }
            SearchableDropdown {
              id: contactPicker; Layout.fillWidth: true; label: "Choose a contact"; options: root.contactOptions
              placeholderText: "Search contacts…"; value: ""; triggerLabel: "Choose from this number’s address book"
              enabled: !root.preview && !action.running && root.contacts.length > 0
              onChanged: function(value) { root.activity(); root.chooseContact(value) }
              onPopupOpenChanged: if (popupOpen) root.activity()
            }
            SearchableDropdown {
              id: countryPicker; Layout.fillWidth: true; label: "Country / calling code"; value: root.countryCode
              options: Countries.options(); placeholderText: "Search country or prefix…"
              enabled: !root.preview && !action.running
              onChanged: function(value) { root.chooseCountry(value) }
              onPopupOpenChanged: if (popupOpen) root.activity()
            }
            PrivateField {
              id: recipient; Layout.fillWidth: true; placeholderText: "Recipient · full international number"; enabled: !root.preview && !action.running; maximumLength: 40
              onTextEdited: root.countryCode = Countries.detect(text, root.countryCode)
            }
            Label { Layout.fillWidth: true; text: "Append the number to the prefix, or paste a full number with +. International format: " + root.recipientNumber }
            Controls.TextArea {
              id: body
              onTextChanged: if (activeFocus) root.activity()
              Layout.fillWidth: true; Layout.preferredHeight: 160
              placeholderText: "Write a message…"; wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
              enabled: !root.preview && !action.running
              color: root.foreground; placeholderTextColor: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.5)
              font.family: Style.font.family; font.pixelSize: Style.font.body
              background: Rectangle { color: "transparent"; radius: 6; border.color: root.foreground; opacity: 0.4 }
            }
            Label { Layout.fillWidth: true; font.bold: true; text: Segments.summary(root.segmentEstimate) + " · estimated" }
            Label { Layout.fillWidth: true; opacity: 0.65; text: "Standard limits: 160/153 GSM-7 units or 70/67 Unicode units. Emoji and some symbols use multiple units. Twilio charges per segment." }
            Label { Layout.fillWidth: true; visible: root.segmentEstimate.tollFree; text: "Toll-free +1 route: conservative multipart estimate using 152/66 units per segment." }
            Label { Layout.fillWidth: true; visible: root.segmentEstimate.unicode.length > 0; text: "Characters requiring Unicode: " + Segments.unicodeDetails(root.segmentEstimate) }
            Label { Layout.fillWidth: true; visible: body.text.length > 1600; text: "Message exceeds this editor’s 1600-unit limit. Shorten it before sending." }
            ActionButton {
              text: root.showPolishPreview ? "Hide Polish-letter preview" : "Preview without Polish letters"
              visible: Segments.withoutPolish(body.text) !== body.text
              enabled: !root.preview && !action.running
              onClicked: root.showPolishPreview = !root.showPolishPreview
            }
            ColumnLayout {
              Layout.fillWidth: true; visible: root.showPolishPreview && !root.preview
              Label { Layout.fillWidth: true; text: root.polishPreview }
              Label { Layout.fillWidth: true; text: Segments.summary(Segments.estimate(root.polishPreview, root.selectedNumber, root.recipientNumber)) + " · estimated" }
              ActionButton { text: "Use this version"; enabled: !action.running; onClicked: { body.text = root.polishPreview; root.showPolishPreview = false } }
            }
            ActionButton {
              text: "Review message"; visible: !root.preview
              enabled: !action.running && !!root.selectedNumber && /^\+[1-9][0-9]{6,14}$/.test(root.recipientNumber) && body.text.trim().length > 0 && body.text.length <= 1600
              onClicked: {
                root.attempted = false
                root.preview = {action: "send", account: root.selectedAccount, from: root.selectedNumber, to: root.recipientNumber, body: body.text,
                  request_id: Date.now().toString() + "-" + Math.random().toString(16).slice(2)}
              }
            }
            Label {
              Layout.fillWidth: true; visible: !!root.preview
              text: root.preview ? "Confirm SMS · " + Segments.summary(Segments.estimate(root.preview.body, root.preview.from, root.preview.to)) + " (estimated)\nFrom: " + root.preview.from + "\nTo: " + root.preview.to + "\n\n" + root.preview.body : ""
            }
            Flow {
              Layout.fillWidth: true; spacing: 8; visible: !!root.preview
              ActionButton {
                text: action.running ? "Sending…" : root.demoMode ? "Simulate send" : "Confirm and send"
                enabled: !action.running && !root.attempted
                onClicked: { root.attempted = true; root.run(root.preview) }
              }
              ActionButton {
                text: root.attempted ? "Start a new message" : "Edit"
                enabled: !action.running
                onClicked: { if (root.attempted) root.resetDraft(); else root.preview = null }
              }
            }
          }

          ColumnLayout {
            id: contactSection
            Layout.fillWidth: true; visible: root.page === "Contacts"; spacing: 10
            Label { Layout.fillWidth: true; text: root.contactsShared ? "Shared address book · all numbers on this account" : "Address book for " + root.selectedNumber }
            PrivateField { id: contactSearch; Layout.fillWidth: true; placeholderText: "Search names or phone numbers…" }
            ActionButton {
              text: "Add contact"; enabled: !!root.selectedNumber && !action.running && !root.preview
              onClicked: { root.clearContactEditor(); root.contactEditorOpen = true; contactNumberField.text = Countries.get(root.defaultCountry).dial }
            }
            ColumnLayout {
              Layout.fillWidth: true; visible: root.contactEditorOpen
              Label { text: root.editingContact ? "Edit contact" : "New contact"; font.bold: true }
              PrivateField { id: contactNameField; Layout.fillWidth: true; placeholderText: "Contact name"; maximumLength: 120 }
              PrivateField { id: contactNumberField; Layout.fillWidth: true; placeholderText: "Phone number with country code, e.g. +12025550120"; maximumLength: 64 }
              RowLayout {
                ActionButton {
                  text: "Save contact"; enabled: !action.running && !poll.running && contactNameField.text.trim().length > 0 && (/^\+[1-9][0-9]{6,14}$/.test(Countries.normalize(contactNumberField.text)) || contactNumberField.text === root.editingContact)
                  onClicked: root.run({action: "save_contact", account: root.selectedAccount, from: root.selectedNumber, original: root.editingContact, name: contactNameField.text.trim(), number: Countries.normalize(contactNumberField.text)})
                }
                ActionButton { text: "Cancel"; enabled: !action.running; onClicked: root.clearContactEditor() }
              }
            }
            Label { text: root.contacts.length + " contacts" }
            Repeater {
              model: root.contacts.filter(function(c) { var query = contactSearch.text.toLowerCase(); return (c.name + " " + c.number).toLowerCase().indexOf(query) >= 0 })
              ColumnLayout {
                id: contactRow
                required property var modelData
                Layout.fillWidth: true
                spacing: Style.space(6)
                Rectangle { Layout.fillWidth: true; height: 1; color: root.foreground; opacity: 0.12 }
                RowLayout {
                  Layout.fillWidth: true
                  Layout.topMargin: Style.space(4); Layout.bottomMargin: Style.space(4)
                  spacing: Style.space(10)
                  ColumnLayout {
                    Layout.fillWidth: true; spacing: Style.space(2)
                    Label { Layout.fillWidth: true; text: contactRow.modelData.name; font.bold: true; maximumLineCount: 1; elide: Text.ElideRight }
                    Label { Layout.fillWidth: true; text: contactRow.modelData.number; opacity: 0.65; maximumLineCount: 1; elide: Text.ElideRight }
                  }
                  ActionButton {
                    text: "SMS"; tooltipText: "Write a message"
                    enabled: !action.running && !root.preview && /^\+[1-9][0-9]{6,14}$/.test(contactRow.modelData.number)
                    onClicked: root.chooseContact(contactRow.modelData.number)
                  }
                  PanelActionButton {
                    iconText: "✎"; tooltipText: "Edit contact"; Accessible.name: "Edit contact"
                    foreground: root.foreground; focusable: true; enabled: !action.running
                    onClicked: {
                      root.activity(); root.editingContact = contactRow.modelData.number
                      contactNameField.text = contactRow.modelData.name; contactNumberField.text = contactRow.modelData.number
                      root.contactEditorOpen = true
                      Qt.callLater(function() { mainScroll.contentItem.contentY = Math.max(0, contactSection.y); contactNameField.forceActiveFocus() })
                    }
                  }
                  PanelActionButton {
                    iconText: "󰆴"; tooltipText: "Delete contact"; Accessible.name: "Delete contact"
                    foreground: root.foreground; focusable: true; enabled: !action.running
                    onClicked: { root.activity(); root.deletingContact = contactRow.modelData.number }
                  }
                }
                RowLayout {
                  Layout.fillWidth: true
                  visible: root.deletingContact === contactRow.modelData.number
                  Label { Layout.fillWidth: true; text: "Delete this contact?" }
                  ActionButton { text: "Delete"; enabled: !action.running && !poll.running; onClicked: root.run({action: "delete_contact", account: root.selectedAccount, from: root.selectedNumber, number: contactRow.modelData.number}) }
                  ActionButton { text: "Cancel"; enabled: !action.running; onClicked: root.deletingContact = "" }
                }
              }
            }
          }
          ColumnLayout {
            Layout.fillWidth: true; visible: root.page === "Settings"; spacing: 10
            Label { text: "Messages & contacts"; font.bold: true }
            SearchableDropdown {
              id: defaultCountryPicker; Layout.fillWidth: true; label: "Default recipient country"
              value: root.defaultCountry; options: Countries.options(); placeholderText: "Search country or prefix…"
              onChanged: function(value) { root.activity(); root.setOption("defaultCountry", value) }
              onPopupOpenChanged: if (popupOpen) root.activity()
            }
            RowLayout {
              Layout.fillWidth: true; visible: !!root.selectedNumber
              Label { Layout.fillWidth: true; text: "Share contacts across this account’s numbers" }
              ToggleSwitch { checked: root.contactsShared; enabled: !action.running && !poll.running; onToggled: root.run({action: "share_contacts", account: root.selectedAccount, from: root.selectedNumber, shared: !checked}) }
            }
            Label { Layout.fillWidth: true; visible: !!root.selectedNumber; text: "The shared book starts as a copy of this number’s contacts. Separate books stay intact and return when sharing is disabled." }
            ActionButton { text: "Import transferred contacts"; visible: root.importPending; enabled: !action.running && !poll.running; onClicked: root.run({action: "import_contacts"}) }
            Label { text: root.encryptedStorage ? "Storage · Encrypted" : "Storage · Standard"; visible: !root.demoMode; font.bold: true }
            ActionButton { text: "Switch to encrypted storage"; visible: !root.demoMode && !root.encryptedStorage; enabled: !action.running && !poll.running && !security.running; onClicked: root.securityRequest({action: "start_encryption", session: root.sessionToken}) }
            Label { visible: !root.demoMode; text: "Incognito"; font.bold: true }
            Label { Layout.fillWidth: true; visible: root.accountList.length > 0; text: root.demoMode ? "Switch off demo to configure your private lock." : (root.encryptedStorage ? "Encrypted SQLCipher storage. Locking closes the vault and clears drafts. Your master password is never saved." : "Standard storage: files are not encrypted. Incognito only hides the panel and clears drafts.") }
            ColumnLayout {
              Layout.fillWidth: true; visible: !root.demoMode
              Label { text: root.incognitoEnabled ? "Enabled · " + root.idleSeconds + " seconds" : "Not enabled" }
              PrivateField { id: currentPassword; Layout.fillWidth: true; visible: root.incognitoEnabled; password: true; placeholderText: "Current password"; maximumLength: 128 }
              PrivateField { id: newPassword; Layout.fillWidth: true; password: true; placeholderText: root.encryptedStorage ? "New master password · leave blank to keep" : "New password · at least 8 characters"; maximumLength: 128 }
              PrivateField { id: confirmPassword; Layout.fillWidth: true; password: true; placeholderText: "Confirm new password"; maximumLength: 128 }
              Label { text: "Lock after inactivity (seconds)" }
              PrivateField { id: timeoutField; Layout.fillWidth: true; text: "60"; placeholderText: "Idle timeout in seconds · 5–3600"; validator: IntValidator { bottom: 5; top: 3600 } }
              Flow {
                Layout.fillWidth: true; spacing: 6
                ActionButton {
                  text: root.incognitoEnabled ? "Update Incognito" : "Enable Incognito"
                  enabled: !security.running && !action.running && (newPassword.text.length >= 8 || (root.encryptedStorage && newPassword.text.length === 0)) && newPassword.text === confirmPassword.text && timeoutField.acceptableInput && (!root.incognitoEnabled || currentPassword.text.length > 0)
                  onClicked: root.securityRequest({action: "configure_security", session: root.sessionToken, enabled: true, password: newPassword.text, current_password: currentPassword.text, timeout: Number(timeoutField.text)})
                }
                ActionButton { text: "Lock now"; visible: root.incognitoEnabled; onClicked: root.lockNow() }
                ActionButton {
                  text: "Disable"; visible: root.incognitoEnabled && !root.encryptedStorage; enabled: !security.running && currentPassword.text.length > 0
                  onClicked: root.securityRequest({action: "configure_security", session: root.sessionToken, enabled: false, current_password: currentPassword.text})
                }
              }
              Label { Layout.fillWidth: true; text: root.securityMessage; visible: text !== "" }
            }
            Label { visible: root.accountList.length > 0; text: "Visible numbers"; font.bold: true }
            Label { Layout.fillWidth: true; visible: root.accountList.length > 0; text: "Choose which numbers appear in the selector, messages and notifications. This is a display preference, not an access lock." }
            Repeater {
              model: root.accountList
              ColumnLayout {
                required property var modelData
                id: visibilityAccount
                Layout.fillWidth: true
                Label { text: visibilityAccount.modelData.name; font.bold: true }
                Repeater {
                  model: visibilityAccount.modelData.numbers
                  ColumnLayout {
                    required property var modelData
                    id: numberSettings
                    Layout.fillWidth: true
                    RowLayout {
                    Layout.fillWidth: true
                    Label { Layout.fillWidth: true; text: numberSettings.modelData.number }
                    ToggleSwitch {
                      checked: root.numbersFor(visibilityAccount.modelData).some(function(n) { return n.number === modelData.number })
                      enabled: !action.running && !poll.running && !root.preview
                      onToggled: root.toggleNumber(visibilityAccount.modelData, modelData.number)
                    }
                    }
                    RowLayout {
                      Layout.fillWidth: true
                      PrivateField { id: numberLabelField; Layout.fillWidth: true; text: root.labelFor(visibilityAccount.modelData.id, numberSettings.modelData); placeholderText: "Number label, e.g. Personal or Work"; maximumLength: 40 }
                      ActionButton { text: "Save label"; enabled: !action.running && !poll.running; onClicked: root.run({action: "set_number_label", account: visibilityAccount.modelData.id, number: numberSettings.modelData.number, label: numberLabelField.text.trim()}) }
                    }
                  }
                }
              }
            }
            Label { text: "Accounts & numbers"; font.bold: true }
            Repeater {
              model: root.accountList
              ColumnLayout {
                required property var modelData
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: modelData.name + " · Connected · " + modelData.numbers.length + " SMS numbers" }
                ActionButton {
                  text: "Disconnect"; visible: root.disconnectCandidate !== modelData.id
                  enabled: !action.running && !poll.running && !root.demoMode
                  onClicked: root.disconnectCandidate = modelData.id
                }
                Label { Layout.fillWidth: true; visible: root.disconnectCandidate === modelData.id; text: "Remove this account’s saved credentials from this computer? Your Twilio account and messages will stay intact." }
                RowLayout {
                  visible: root.disconnectCandidate === modelData.id
                  ActionButton { text: "Confirm disconnect"; enabled: !action.running && !poll.running; onClicked: root.run({action: "disconnect_account", account: modelData.id}) }
                  ActionButton { text: "Cancel"; enabled: !action.running; onClicked: root.disconnectCandidate = "" }
                }
              }
            }
            ActionButton { text: "Sync numbers"; enabled: !poll.running && !action.running; onClicked: root.refresh(true) }
            Label { Layout.fillWidth: true; text: "Numbers are discovered from your Twilio account. Add a number in the Twilio Console, then sync here." }
            ActionButton { text: root.accountSetupOpen ? "Cancel account setup" : root.accountList.length ? "Add Twilio account" : "Connect Twilio account"; onClicked: { if (root.accountSetupOpen) root.clearCredentials(); else root.accountSetupOpen = true } }
            ColumnLayout {
            Layout.fillWidth: true; visible: root.accountSetupOpen; spacing: 8
            Label { Layout.fillWidth: true; text: "Connect a Twilio account. This form does not add a phone number. Find Account SID and Auth Token in your Twilio Console." }
            Label { text: "Account label" }
            PrivateField { id: accountName; Layout.fillWidth: true; placeholderText: "Choose a name, e.g. Personal or Work"; maximumLength: 60; enabled: !action.running }
            Label { text: "Twilio Account SID" }
            PrivateField { id: sid; Layout.fillWidth: true; placeholderText: "Account SID · AC…"; password: true; maximumLength: 34; enabled: !action.running }
            Label { text: "Twilio Auth Token" }
            PrivateField { id: token; Layout.fillWidth: true; placeholderText: "API credential from Twilio, not your Incognito password"; password: true; maximumLength: 32; enabled: !action.running }
            ActionButton {
              text: root.demoMode ? "Simulate account setup" : "Connect account"
              enabled: !action.running && (root.demoMode || (accountName.text.trim().length > 0 && sid.text.length === 34 && token.text.length === 32))
              onClicked: root.run({action: "add_account", name: accountName.text, sid: sid.text, token: token.text})
            }
            Label { Layout.fillWidth: true; text: "Credentials stay in a private local file. Using an existing Account SID updates that account’s label and token." }
            }
            RowLayout {
              Layout.fillWidth: true
              Label { Layout.fillWidth: true; text: "Notifications (without message content)" }
              ToggleSwitch { checked: root.setting("notifyNewMessages", true); onToggled: root.setOption("notifyNewMessages", !checked) }
            }
            Label { text: "Refresh interval" }
            Flow {
              Layout.fillWidth: true; spacing: 6
              Repeater {
                model: [15, 30, 60, 120]
                ActionButton { required property int modelData; text: modelData + "s"; selected: Number(root.setting("refreshIntervalSec", 30)) === modelData; onClicked: root.setOption("refreshIntervalSec", modelData) }
              }
            }
            RowLayout {
              Layout.fillWidth: true
              Label { Layout.fillWidth: true; text: "Demo mode · fictional data only" }
              ToggleSwitch { checked: root.demoMode; enabled: !poll.running && !action.running; onToggled: root.setOption("demo", !checked) }
            }
          }
          Label { Layout.fillWidth: true; opacity: 0.5; text: "Bananilio 0.1.0 · Local Twilio SMS client" }
          }
        }
      }
    }
  }
}
