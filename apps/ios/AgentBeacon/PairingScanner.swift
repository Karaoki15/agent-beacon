import AVFoundation
import SwiftUI

struct PairingScanner: UIViewControllerRepresentable {
    let onCode: (String) -> Void
    let onFailure: (String) -> Void

    func makeUIViewController(context: Context) -> ScannerController {
        let controller = ScannerController()
        controller.onCode = onCode
        controller.onFailure = onFailure
        return controller
    }

    func updateUIViewController(_ uiViewController: ScannerController, context: Context) {}
}

final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var onCode: ((String) -> Void)?
    var onFailure: ((String) -> Void)?

    private let captureSession = AVCaptureSession()
    private let cameraQueue = DispatchQueue(label: "com.example.AgentBeacon.camera")
    private var previewLayer: AVCaptureVideoPreviewLayer?
    private var visible = false
    private var authorizationRequested = false
    private var delivered = false

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        cameraQueue.async { [weak self] in
            guard let self else { return }
            self.visible = true
            self.configureCamera()
        }
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        previewLayer?.frame = view.bounds
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        cameraQueue.async { [weak self] in
            guard let self else { return }
            self.visible = false
            if self.captureSession.isRunning { self.captureSession.stopRunning() }
        }
    }

    deinit {
        let session = captureSession
        cameraQueue.async {
            if session.isRunning { session.stopRunning() }
        }
    }

    private func configureCamera() {
        guard visible else { return }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            startSessionOnCameraQueue()
        case .notDetermined:
            guard !authorizationRequested else { return }
            authorizationRequested = true
            AVCaptureDevice.requestAccess(for: .video) { [weak self] allowed in
                guard let self else { return }
                self.cameraQueue.async {
                    guard self.visible else { return }
                    if allowed { self.startSessionOnCameraQueue() }
                    else { self.reportFailure("Для сканування QR-коду потрібен доступ до камери.") }
                }
            }
        default:
            reportFailure("Дозвольте Agent Beacon доступ до камери в Параметрах або вставте посилання вручну.")
        }
    }

    private func startSessionOnCameraQueue() {
        guard visible, !captureSession.isRunning else { return }
        guard let camera = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: camera),
              captureSession.canAddInput(input) else {
            reportFailure("Камера недоступна. Вставте посилання для підключення вручну.")
            return
        }
        captureSession.addInput(input)
        let output = AVCaptureMetadataOutput()
        guard captureSession.canAddOutput(output) else {
            reportFailure("Сканування QR-кодів недоступне на цьому пристрої.")
            return
        }
        captureSession.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: cameraQueue)
        output.metadataObjectTypes = [.qr]
        DispatchQueue.main.async { [weak self] in
            guard let self, self.isViewLoaded, self.view.window != nil else { return }
            let layer = AVCaptureVideoPreviewLayer(session: self.captureSession)
            layer.videoGravity = .resizeAspectFill
            layer.frame = self.view.bounds
            self.view.layer.addSublayer(layer)
            self.previewLayer = layer
        }
        captureSession.startRunning()
    }

    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject],
                        from connection: AVCaptureConnection) {
        guard visible, !delivered, let object = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
              object.type == .qr, let value = object.stringValue else { return }
        delivered = true
        captureSession.stopRunning()
        DispatchQueue.main.async { [weak self] in self?.onCode?(value) }
    }

    private func reportFailure(_ text: String) {
        DispatchQueue.main.async { [weak self] in self?.onFailure?(text) }
    }
}
