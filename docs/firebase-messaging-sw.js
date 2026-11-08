/* Firebase Cloud Messaging background handler, scoped to /Akciofigyelo/ */
importScripts('https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.14.1/firebase-messaging-compat.js');
firebase.initializeApp({apiKey:'AIzaSyD_9uSd6F7TXVkBZoMEQPd2bCrwsECwdNY',authDomain:'akciofigyelo-8dffc.firebaseapp.com',projectId:'akciofigyelo-8dffc',storageBucket:'akciofigyelo-8dffc.firebasestorage.app',messagingSenderId:'648460189102',appId:'1:648460189102:web:e0360953351f7844dd5c3f'});
const messaging=firebase.messaging();
/* Firebase displays notification payloads in background automatically. For data-only payloads: */
messaging.onBackgroundMessage(payload=>{
  if(payload.notification)return; // avoid double notifications
  const title=payload.data?.title||'Akciófigyelő';
  const body=payload.data?.body||'Új akció érkezett';
  const url=payload.data?.url||'./';
  self.registration.showNotification(title,{body,data:{url},tag:payload.data?.id||'akciofigyelo'});
});
self.addEventListener('notificationclick',event=>{event.notification.close();const target=event.notification.data?.url||self.registration.scope;event.waitUntil(clients.openWindow(new URL(target,self.registration.scope).href));});
