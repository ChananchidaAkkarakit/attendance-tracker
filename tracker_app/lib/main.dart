// lib/main.dart
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:camera/camera.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'config.dart';

late final List<CameraDescription> _cameras;

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  _cameras = await availableCameras(); // เตรียมรายการกล้อง
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Tracker App',
      theme: ThemeData(useMaterial3: true, colorSchemeSeed: Colors.indigo),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});
  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final codeCtrl = TextEditingController();
  CameraController? cam;
  bool camReady = false;
  bool loading = false;
  String logMsg = '';
  String lastImageB64 = '';

  @override
  void initState() {
    super.initState();
    _initCamera();
  }

  Future<void> _initCamera() async {
    try {
      // เลือกกล้องหน้า ถ้าไม่มีก็ใช้ตัวแรก
      // init camera
      final desc = _cameras.firstWhere(
        (c) => c.lensDirection == CameraLensDirection.front,
        orElse: () => _cameras.first,
      );
      cam = CameraController(
        desc,
        ResolutionPreset.medium,
        enableAudio: false,
        imageFormatGroup: ImageFormatGroup.jpeg, // << บังคับ JPEG
      );
      await cam!.initialize();
      setState(() => camReady = true);
    } catch (e) {
      setState(() => logMsg = '❌ เปิดกล้องไม่สำเร็จ: $e');
    }
  }

  @override
  void dispose() {
    cam?.dispose();
    super.dispose();
  }

  // ----- Location -----
  Future<void> _ensureLocationPermission() async {
    final enabled = await Geolocator.isLocationServiceEnabled();
    if (!enabled) throw Exception('โปรดเปิด Location Service');
    var perm = await Geolocator.checkPermission();
    if (perm == LocationPermission.denied) {
      perm = await Geolocator.requestPermission();
    }
    if (perm == LocationPermission.denied ||
        perm == LocationPermission.deniedForever) {
      throw Exception('ไม่ได้รับสิทธิ์ตำแหน่ง');
    }
  }

  Future<({double lat, double lng, double accuracy})> _getLocation() async {
    await _ensureLocationPermission();
    final pos = await Geolocator.getCurrentPosition(
      desiredAccuracy: LocationAccuracy.best,
    );
    return (lat: pos.latitude, lng: pos.longitude, accuracy: pos.accuracy);
  }

  // ----- Camera capture -> base64 -----
  Future<String?> _captureBase64() async {
    if (cam == null || !camReady || cam!.value.isTakingPicture) return null;
    final shot = await cam!.takePicture();
    final bytes = await shot
        .readAsBytes(); // << แทน File(shot.path).readAsBytes()
    return 'data:image/jpeg;base64,${base64Encode(bytes)}';
  }

  // ----- HTTP helper -----
  Future<Map<String, dynamic>> _post(
    String path,
    Map<String, dynamic> body,
  ) async {
    final res = await http.post(
      Uri.parse('$backendBaseUrl$path'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(body),
    );
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  // ----- Enroll: ถ่าย 4 รูปติดกัน -----
  Future<void> enroll() async {
    final code = codeCtrl.text.trim();
    if (code.isEmpty) {
      setState(() => logMsg = '❌ กรอกรหัส');
      return;
    }
    if (!camReady) {
      setState(() => logMsg = '❌ กล้องยังไม่พร้อม');
      return;
    }

    setState(() {
      loading = true;
      logMsg = 'กำลังถ่าย 4 รูป...';
    });
    try {
      final images = <String>[];
      for (int i = 0; i < 4; i++) {
        final b64 = await _captureBase64();
        if (b64 != null) images.add(b64);
        await Future.delayed(const Duration(milliseconds: 200));
      }
      if (images.isEmpty) {
        setState(() => logMsg = '❌ ถ่ายรูปไม่สำเร็จ');
        return;
      }

      final j = await _post('/api/enroll', {'code': code, 'images': images});
      setState(
        () => logMsg = j['ok'] == true
            ? '✅ Enrolled $code (${j['templates']})'
            : '❌ ${j['msg']}',
      );
    } catch (e) {
      setState(() => logMsg = '❌ $e');
    } finally {
      setState(() => loading = false);
    }
  }

  // ----- Recognize: ถ่าย 1 รูป + ส่งพิกัด -----
  Future<void> recognize(String type) async {
    if (!camReady) {
      setState(() => logMsg = '❌ กล้องยังไม่พร้อม');
      return;
    }
    setState(() {
      loading = true;
      logMsg = 'กำลังสแกน...';
    });
    try {
      final img = await _captureBase64();
      if (img == null) {
        setState(() => logMsg = '❌ ถ่ายรูปไม่สำเร็จ');
        return;
      }
      lastImageB64 = img;

      final loc = await _getLocation();
      final payload = {
        'image': img,
        'type': type, // "checkin" | "checkout"
        'threshold': 0.58, // ปรับได้
        'lat': loc.lat,
        'lng': loc.lng,
        'accuracy': loc.accuracy,
      };

      final j = await _post('/api/recognize', payload);
      final gf = (j['geofence'] ?? {}) as Map;
      final msg = (j['matched'] == true && gf['within'] == true)
          ? '✅ ${j['name']} (${j['score']}) • ${j['period'] ?? "-"} • ${gf['distance_m']} m'
          : '⛔ ${gf['reason'] ?? 'reject'} • matched=${j['matched']}';

      setState(() => logMsg = msg);
    } catch (e) {
      setState(() => logMsg = '❌ $e');
    } finally {
      setState(() => loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final preview = (cam != null)
        ? AspectRatio(
            aspectRatio: cam!.value.aspectRatio,
            child: camReady
                ? CameraPreview(cam!)
                : const Center(child: CircularProgressIndicator()),
          )
        : const SizedBox.shrink();

    return Scaffold(
      appBar: AppBar(title: const Text('Tracker App')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
            controller: codeCtrl,
            decoration: const InputDecoration(
              labelText: 'Code (รหัสพนักงาน/นักเรียน)',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          preview,
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed: loading ? null : enroll,
            icon: const Icon(Icons.person_add_alt_1),
            label: const Text('Enroll (ถ่าย 4 รูป)'),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: loading ? null : () => recognize('checkin'),
                  icon: const Icon(Icons.login),
                  label: const Text('Check-in'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: loading ? null : () => recognize('checkout'),
                  icon: const Icon(Icons.logout),
                  label: const Text('Check-out'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Text(logMsg),
          const SizedBox(height: 8),
          if (lastImageB64.isNotEmpty)
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: Image.memory(
                base64Decode(lastImageB64.split(',').last),
                height: 200,
                fit: BoxFit.cover,
              ),
            ),
        ],
      ),
    );
  }
}
