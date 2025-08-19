let stream=null;

async function startCamera(){
  try{
    const v=document.getElementById('cam');
    stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:"user",width:640,height:480}});
    v.srcObject=stream; document.getElementById('camStatus').innerText='✅ กล้องทำงาน';
  }catch(e){ document.getElementById('camStatus').innerText='❌ '+e.message; }
}

function stopCamera(){
  if(stream){stream.getTracks().forEach(t=>t.stop()); stream=null;}
  document.getElementById('camStatus').innerText='หยุดกล้องแล้ว';
}

function snapBase64(){
  const v=document.getElementById('cam'); const c=document.createElement('canvas');
  c.width=v.videoWidth||640; c.height=v.videoHeight||480;
  c.getContext('2d').drawImage(v,0,0); return c.toDataURL('image/jpeg',0.8);
}

const wait=ms=>new Promise(r=>setTimeout(r,ms));

async function doEnroll(){
  const code=document.getElementById('code').value.trim();
  const n=parseInt(document.getElementById('frames').value,10)||6;
  if(!code) return document.getElementById('enrollMsg').innerText='กรอกรหัสก่อน';
  document.getElementById('enrollMsg').innerText='กำลังถ่าย '+n+' เฟรม...';
  const frames=[];
  for(let i=0;i<n;i++){ frames.push(snapBase64()); await wait(150); }
  const res=await fetch('/api/enroll',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code,images:frames})
  }).then(r=>r.json()).catch(e=>({ok:false,msg:e.message}));
  document.getElementById('enrollMsg').innerText=res.ok?`✅ ${res.msg}`:`❌ ${res.msg||'enroll ล้มเหลว'}`;
}

/* ---------- เพิ่ม: ขอพิกัดครั้งเดียวตอนสแกน ---------- */
function getLocationOnce(){
  return new Promise((resolve,reject)=>{
    if(!('geolocation' in navigator)) return reject(new Error('เบราว์เซอร์ไม่รองรับ Geolocation'));
    navigator.geolocation.getCurrentPosition(
      pos=>resolve({lat:pos.coords.latitude,lng:pos.coords.longitude,accuracy:pos.coords.accuracy}),
      err=>reject(err),
      { enableHighAccuracy:true, timeout:10000, maximumAge:0 }
    );
  });
}

/* (ไม่บังคับ) แปลเหตุผล geofence ให้เข้าใจง่าย */
function humanReason(reason){
  switch(reason){
    case 'ok': return 'อยู่ในรัศมี';
    case 'gps_accuracy_poor': return 'ความแม่นยำ GPS ต่ำ';
    case 'no_sites_configured': return 'ยังไม่ได้ตั้งจุดอนุญาต';
    case 'outside_radius': return 'อยู่นอกรัศมี';
    case 'face_not_matched': return 'จำหน้าไม่สำเร็จ';
    default: return reason||'ไม่ทราบสาเหตุ';
  }
}

async function scanOnce(){
  const msgEl=document.getElementById('scanMsg');
  msgEl.innerText='กำลังสแกน...';
  try{
    const img=snapBase64();
    const type=document.getElementById('type').value;
    const thEl=document.getElementById('threshold')||document.getElementById('th'); // รองรับ id th ของคุณ
    const threshold=thEl?parseFloat(thEl.value||'0.58'):undefined;

    // ขอพิกัด
    const loc=await getLocationOnce(); // {lat,lng,accuracy}
    // (ตัวเลือกฝั่ง client) บล็อคถ้า accuracy แย่เกินไป
    // if(loc.accuracy>50) { msgEl.innerText='❌ ความแม่นยำ GPS ต่ำ (>50m)'; return; }

    const payload={ image:img, type, lat:loc.lat, lng:loc.lng, accuracy:loc.accuracy };
    if(threshold!==undefined) payload.threshold=threshold;

    const res=await fetch('/api/recognize',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)
    }).then(r=>r.json()).catch(e=>({ok:false,msg:e.message}));

    if(!res.ok){ msgEl.innerText='❌ '+(res.msg||'สแกนล้มเหลว'); return; }

    const gf=res.geofence||{};
    if(res.matched && gf.within){
      msgEl.innerText=`✅ ${res.name} | score=${res.score} | ในรัศมี ~${gf.distance_m??0} m`;
    }else{
      const dist=gf.distance_m!=null?` (ห่าง ~${gf.distance_m} m)`:''; 
      msgEl.innerText=`❌ ${res.matched?res.name:'Unknown'} | score=${res.score} | ${humanReason(gf.reason)}${dist}`;
    }
  }catch(e){
    msgEl.innerText='❌ '+(e.message||'เกิดข้อผิดพลาด');
  }
}
