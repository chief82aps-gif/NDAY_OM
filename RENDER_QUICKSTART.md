# Quick Start: Deploy to Render + Vercel + Custom Domain

You have a Render account - here's the fastest path to live! ⚡

## **5-Minute Checklist**

### ✅ What You'll Need
- [ ] GitHub account (ensure DSP_OM repo is pushed)
- [ ] Render.com account (you have this!)
- [ ] Vercel account (free, ~2 minutes to create)
- [ ] newdaylogisticsllc.com domain (existing)

---

## **Phase 1: Push to GitHub (2 minutes)**

```powershell
cd c:\Users\chief\NDAY_OM
git push origin master
```

✅ This uploads all code to GitHub for both Render and Vercel to access.

---

## **Phase 2: Deploy Backend to Render (5 minutes)**

### In Your Browser:

1. **Go to dashboard.render.com**
2. **Click "New +" button** → Select **"Web Service"**
3. **Connect GitHub** (if not already connected)
4. **Select Repository**: Choose `DSP_OM`
5. **Configure**:
   - Name: `dsp-om-backend` (or any name)
   - Runtime: Python 3 (auto-detected)
   - Build Command: `pip install -r api/requirements.txt`
   - Start Command: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
   - **OR** leave empty if Render auto-reads Procfile (it will!)
6. **Environment**: Leave blank (no env vars needed)
7. **Click "Create Web Service"**

✅ **Render starts deploying** - takes ~2 minutes
- Watch the log output in real-time
- When "Service started successfully" appears, you're done!

**Copy your Render URL**: `https://dsp-om-backend.onrender.com` (or your service name)

---

## **Phase 3: Deploy Frontend to Vercel (5 minutes)**

### In Your Browser:

1. **Go to vercel.com/dashboard**
2. **Click "Add New..."** → **"Project"**
3. **Import Git Repository**
   - Search for `DSP_OM`
   - Click "Import"
4. **Configure Project**:
   - Framework Preset: **Next.js**
   - Root Directory: **frontend** ← IMPORTANT!
   - Leave other settings default
5. **Environment Variables**:
   - Click "Add Environment Variable"
   - Name: `NEXT_PUBLIC_API_URL`
   - Value: `https://dsp-om-backend.onrender.com` (from Render)
6. **Click "Deploy"**

✅ **Vercel deploys** - takes ~2 minutes

Get your Vercel preview URL (temporary, for testing)
```
https://dsp-om.vercel.app
```

---

## **Phase 4: Add Custom Domain (5 minutes)**

### At Vercel:

1. **In your Vercel project**
2. **Go to Settings** → **Domains**
3. **Add Domain**:
   - Enter: `newdaylogisticsllc.com`
   - Click "Add"

Vercel shows you the CNAME record needed.

### At Your Domain Registrar:

1. **Log into your domain registrar** (GoDaddy, Namecheap, etc.)
2. **Find DNS Settings**
3. **Add CNAME Record**:
   ```
   Type: CNAME
   Name: @ (or root)
   Value: cname.vercel-dns.com.
   TTL: 3600
   ```
4. **Save changes**

⏳ **Wait 5-10 minutes** for DNS to propagate

---

## **Phase 5: Verify It Works (2 minutes)**

Once domain is live:

1. **Visit**: https://newdaylogisticsllc.com
2. **Should see**: The NDAY upload interface
3. **Test it**:
   - Upload DOP Excel
   - Upload Fleet Excel
   - Upload Cortex Excel
   - Upload Route Sheets PDF
   - Click "Assign Vehicles" → Should show 35/35 success
   - Click "Generate Handouts" → PDF appears
   - Click "Download PDF" → File downloads

✅ **You're LIVE!** 🚀

---

## **Monitoring & Logs**

### Render Backend:
- **Dashboard**: https://dashboard.render.com
- **Logs**: Click your service → "Logs" tab
- Real-time output of server activity
- Errors appear here first

### Vercel Frontend:
- **Dashboard**: https://vercel.com/dashboard
- **Logs**: Click your project → "Logs" tab
- **Analytics**: See traffic and performance

---

## **Costs**

| Item | Cost | Notes |
|------|------|-------|
| Render Free Tier | $0 | 750 free compute hours/month |
| Vercel | $0 | Free forever for hobby projects |
| Domain | $12/year | Existing (newdaylogisticsllc.com) |
| **Total** | **$1/month** | Using free tiers |

If you exceed Render's free tier (750 hrs), moves to $7/month.
Most low-traffic apps stay on free tier.

---

## **Troubleshooting**

### ❌ Backend won't deploy
→ Check Render logs for Python errors  
→ Verify `Procfile` exists  
→ Verify `api/requirements.txt` exists

### ❌ Frontend can't connect
→ Check `NEXT_PUBLIC_API_URL` is correct  
→ Verify backend URL works: visit `https://dsp-om-backend.onrender.com/upload/status`
→ Check browser console (F12) for errors

### ❌ Domain shows "Not Found"
→ Check DNS CNAME was added correctly  
→ Wait 10-15 minutes more for propagation  
→ Clear browser cache (Ctrl+Shift+Delete)  
→ Try in incognito window

### ❌ PDF upload fails
→ Check file size (should be small for testing)  
→ Verify backend is running (check Render logs)  
→ Try uploading just one file type first

---

## **Next Steps (Optional)**

After you're live, consider:

1. **Database**: Add PostgreSQL for historical data
2. **Analytics**: Track PDF generations over time
3. **Monitoring**: Set up alerts for errors
4. **Scaling**: Render auto-scales if needed

---

## **Need Help?**

- **Render Docs**: https://docs.render.com
- **Vercel Docs**: https://vercel.com/docs
- **FastAPI Docs**: https://fastapi.tiangolo.com

---

**You're ready!** 🎉 Follow the 5 phases above and you'll be live in 20 minutes.
