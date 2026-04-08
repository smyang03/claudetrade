@echo off
schtasks /delete /tn "claudetrade_kr" /f 2>nul
schtasks /delete /tn "claudetrade_us" /f 2>nul

schtasks /create /tn "claudetrade_kr_open"  /tr "C:\Users\Unknown\anaconda3\envs\upbit\python.exe E:\code\claudetrade\update_data.py --market KR" /sc daily /st 08:30 /f
schtasks /create /tn "claudetrade_kr_close" /tr "C:\Users\Unknown\anaconda3\envs\upbit\python.exe E:\code\claudetrade\update_data.py --market KR" /sc daily /st 16:00 /f
schtasks /create /tn "claudetrade_us_open"  /tr "C:\Users\Unknown\anaconda3\envs\upbit\python.exe E:\code\claudetrade\update_data.py --market US" /sc daily /st 22:00 /f
schtasks /create /tn "claudetrade_us_close" /tr "C:\Users\Unknown\anaconda3\envs\upbit\python.exe E:\code\claudetrade\update_data.py --market US" /sc daily /st 07:00 /f

echo.
echo === 등록 결과 확인 ===
schtasks /query /tn "claudetrade_kr_open"
schtasks /query /tn "claudetrade_kr_close"
schtasks /query /tn "claudetrade_us_open"
schtasks /query /tn "claudetrade_us_close"
