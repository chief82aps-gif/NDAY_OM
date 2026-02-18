# NDAY OM - DEPLOYMENT GUIDE

## Quick Deploy (Render.com + Vercel)

### **Backend Deployment to Render (5 minutes)

1. Go to https://render.com
2. Sign in with your account
3. Click "New +" → "Web Service"
4. Connect GitHub and select `DSP_OM` repository
5. Render auto-detects Python + Procfile
6. No env variables needed (uses defaults)
7. Click "Create Web Service"
8. Get your Render domain: `https://your-app.onrender.com`

### **Frontend Deployment to Vercel (10 minutes)**

1. Go to https://vercel.com
2. Click "New Project" → Import Git repo
3. Select `DSP_OM` repository
4. Set build settings:
   - **Framework**: Next.js
   - **Root Directory**: `frontend`
   - **Build Command**: `npm run build`
   - **Output Directory**: `.next`
5. Add environment variable:
   - `NEXT_PUBLIC_API_URL=https://your-app.onrender.com` (from Render)
6. Click "Deploy"
7. Get your Vercel domain (e.g., `dsp-om.vercel.app`)

### **Domain Setup (newdaylogisticsllc.com)**

1. Go to your domain registrar
2. Update DNS to point to Vercel:
   - Type: `CNAME`
   - Name: `@` (or root)
   - Value: `cname.vercel-dns.com.`
3. Go to Vercel dashboard → Settings → Domains
4. Add `newdaylogisticsllc.com`
5. Wait 5-10 minutes for SSL certificate

### **Issues & Troubleshooting**

**Backend not uploading to Render:**
- Check Python version matches `runtime.txt` (3.11)
- Ensure `Procfile` exists in root directory
- Check `requirements.txt` has all dependencies
- Render dashboard → Logs tab shows deployment progress

**Frontend can't reach backend:**
- Verify `NEXT_PUBLIC_API_URL` environment variable
- Update `/frontend/lib/backendFetch.ts` to use the env variable
- Check CORS is enabled in FastAPI (it is)

**PDF uploads failing:**
- Railway has limited storage - consider AWS S3 integration
- Current: 512MB limit per deployment

## **Recommended Architecture**

```
User Browser
    ↓
newdaylogisticsllc.com (Vercel frontend)
    ↓
API → https://your-app.railway.app (Backend)
    ↓
File Processing (DOP, Fleet, Cortex, Route Sheets)
    ↓
PDF Generation → Download to user
```

## **What's Included**

✅ Procfile (for Railway/Heroku)
✅ runtime.txt (Python 3.11)
✅ requirements.txt (all dependencies)
✅ Next.js frontend (SSR ready)
✅ FastAPI backend (CORS enabled)
✅ PDF generation (ReportLab)

## **Cost Estimate**
- Railway: $5-20/month (pay-as-you-go)
- Vercel: Free ($0/month for hobby)
- Domain: $12/year (existing)
- **Total: ~$5-20/month**

## **Next Steps**

1. Push to GitHub
2. Follow Railway deployment steps
3. Follow Vercel deployment steps
4. Update DNS nameservers
5. Test live at newdaylogisticsllc.com

Questions? Check railway.app and vercel.com docs for platform-specific help.
