import cv2
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import numpy as np
from datetime import datetime
from moviepy.editor import VideoFileClip, CompositeVideoClip, concatenate_videoclips
import moviepy.video.fx.all as vfx  

from proglog import ProgressBarLogger

class MoviePyProgressListener(ProgressBarLogger):
    def __init__(self, log_signal):
        super().__init__()
        self.log_signal = log_signal

    def callback(self, **changes):
        bars = self.state.get('bars', {})
        # MoviePy 新版可能用 'frame' 或 't'
        for key in ('t', 'frame'):
            if key in bars:
                bar = bars[key]
                total = bar.get('total') or bar.get('duration')
                current = bar.get('index') or bar.get('current')
                if total and total > 0 and current is not None:
                    p = int((current / total) * 100)
                    self.log_signal.emit(f"RENDER_PROGRESS:{max(0, min(100, p))}")
                    return
class VideoProcessor:
    """视频处理器：极速导出 + 全量渲染，支持实时取消"""

    def __init__(self, log_signal=None):
        """
        :param log_signal: PyQt 的信号对象，用于向 UI 发送实时日志
        """
        self.log_signal = log_signal
        self.is_cancelled = False
        self.current_subprocess = None      # 保存当前子进程对象
        self.current_out_path = None         # 保存当前输出文件路径

    def fast_remux_process(self, task, config):
        """高兼容性导出函数：支持实时取消"""

        def stream_ffmpeg_progress(cmd, start_pct, end_pct, duration):
            """运行ffmpeg并实时监控进度，支持取消"""
            try:
                # 启动子进程
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    encoding='utf-8',
                    errors='ignore',
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                
                # 保存子进程引用，用于取消时终止
                self.current_subprocess = process
                
                time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
                
                while True:
                    if self.is_cancelled:
                        process.terminate()
                        process.wait()
                        self.current_subprocess = None
                        return False
                    
                    line = process.stderr.readline()
                    if not line and process.poll() is not None:
                        break
                    
                    if "time=" in line:
                        match = time_pattern.search(line)
                        if match and duration > 0:
                            hours, minutes, seconds = map(float, match.groups())
                            current_seconds = hours * 3600 + minutes * 60 + seconds
                            phase_progress = min(1.0, current_seconds / duration)
                            actual_progress = int(start_pct + (end_pct - start_pct) * phase_progress)
                            self.log_signal.emit(f"RENDER_PROGRESS:{max(0, min(99, actual_progress))}")
                
                process.wait()
                self.current_subprocess = None
                return process.returncode == 0
                
            except Exception as e:
                self.emit_log(f"进度监控异常: {str(e)}")
                return False

        try:
            from utils.ffmpeg_utils import get_ffmpeg_path
            ffmpeg_exe = get_ffmpeg_path()
        except Exception:
            import logging; logging.getLogger("CreativeEnginePro").debug("ffmpeg path resolution failed", exc_info=True)
            ffmpeg_exe = 'ffmpeg.exe'

        tmpdir = None  # 临时目录
        try:
            input_path = task['path']
            tail_path = config.get('tail_path')
            out_dir = config.get('out_dir', 'output')
            
            if not os.path.exists(out_dir):
                os.makedirs(out_dir)

            base_name = os.path.splitext(os.path.basename(input_path))[0]
            final_name = config.get('final_save_name', f"{base_name}_processed")
            out_path = os.path.join(out_dir, f"{final_name}.mp4")
            
            # 自动重命名防覆盖
            counter = 1
            while os.path.exists(out_path):
                out_path = os.path.join(out_dir, f"{final_name}_{counter}.mp4")
                counter += 1
            
            # 记录输出路径,用于取消时删除
            self.current_out_path = out_path

            cut_time = task.get('precise_cut_time', task.get('duration', 5))
            if cut_time <= 0:
                cut_time = 5

            si = None
            if sys.platform == "win32":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            if self.is_cancelled:
                return False

            # 有尾页：先各自转码，再 concat 拼接
            if tail_path and os.path.exists(tail_path):
                tmpdir = tempfile.mkdtemp()
                tmp_main = os.path.join(tmpdir, 'main_part.mp4')
                tmp_tail = os.path.join(tmpdir, 'tail_part.mp4')
                concat_txt = os.path.join(tmpdir, 'concat.txt')

                # 获取主视频参数
                cap = cv2.VideoCapture(input_path)
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = cap.get(cv2.CAP_PROP_FPS)
                cap.release()
                
                if fps <= 0 or np.isnan(fps):
                    fps = 30
                w = w if w % 2 == 0 else w + 1
                h = h if h % 2 == 0 else h + 1

                # 1. 转码主视频
                self.emit_log(f"正在处理主视频部分...")
                cmd_main = [
                    ffmpeg_exe, '-y',
                    '-ss', '0',
                    '-t', str(cut_time),
                    '-i', input_path,
                    '-r', str(fps),
                    '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p',
                    '-preset', 'fast',
                    '-crf', '23',
                    '-c:a', 'aac',
                    '-ar', '44100', 
                    tmp_main
                ]
                
                if self.is_cancelled:
                    return False
                    
                if not stream_ffmpeg_progress(cmd_main, 0, 70, float(cut_time)):
                    self.emit_log("主视频转码失败")
                    return False

                # 2. 转码尾页
                self.emit_log(f"正在处理尾页部分...")
                cap_t = cv2.VideoCapture(tail_path)
                t_fps = cap_t.get(cv2.CAP_PROP_FPS)
                t_frames = cap_t.get(cv2.CAP_PROP_FRAME_COUNT)
                tail_duration = t_frames / t_fps if t_fps > 0 else 2
                cap_t.release()

                cmd_tail = [
                    ffmpeg_exe, '-y',
                    '-i', tail_path,
                    '-vf', f'scale={w}:{h}',
                    '-r', str(fps),
                    '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p',
                    '-preset', 'fast',
                    '-crf', '23',
                    '-c:a', 'aac',
                    '-ar', '44100',
                    tmp_tail
                ]
                
                if self.is_cancelled:
                    return False
                    
                if not stream_ffmpeg_progress(cmd_tail, 70, 90, tail_duration):
                    self.emit_log("尾页转码失败")
                    return False

                # 3. concat 拼接
                self.emit_log(f"正在合并视频...")
                with open(concat_txt, 'w', encoding='utf-8') as f:
                    f.write(f"file '{tmp_main.replace(os.sep, '/')}'\n")
                    f.write(f"file '{tmp_tail.replace(os.sep, '/')}'\n")

                cmd_concat = [
                    ffmpeg_exe, '-y',
                    '-f', 'concat', '-safe', '0',
                    '-i', concat_txt,
                    '-c', 'copy',
                    out_path
                ]
                
                if self.is_cancelled:
                    return False
                    
                r3 = subprocess.run(cmd_concat, capture_output=True, encoding='utf-8', errors='ignore', startupinfo=si)
                
                if r3.returncode != 0:
                    self.emit_log(f"拼接失败: {r3.stderr[-300:]}")
                    return False

            else:
                # 无尾页：单一转码
                self.emit_log(f"正在极速导出...")
                cmd = [
                    ffmpeg_exe, '-y',
                    '-ss', '0',
                    '-t', str(cut_time),
                    '-i', input_path,
                    '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p',
                    '-preset', 'fast',
                    '-crf', '23',
                    '-c:a', 'aac',
                    out_path
                ]
                
                if self.is_cancelled:
                    return False
                    
                if not stream_ffmpeg_progress(cmd, 0, 100, float(cut_time)):
                    return False

            self.log_signal.emit("RENDER_PROGRESS:100")
            self.emit_log(f"导出成功: {final_name}")
            return True

        except Exception as e:
            self.emit_log(f"导出异常: {str(e)}")
            return False
        finally:
            # 清理临时目录
            if tmpdir and os.path.exists(tmpdir):
                try:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                except Exception:
                    import logging; logging.getLogger("CreativeEnginePro").debug("tmpdir cleanup failed", exc_info=True)
    def kill_current_process(self):
        """终止当前正在运行的 ffmpeg 进程并清理资源"""
        if self.current_subprocess and self.current_subprocess.poll() is None:
            try:
                self.current_subprocess.terminate()
                # 等待进程结束，最多 2 秒
                self.current_subprocess.wait(timeout=2)
            except Exception:
                import logging; logging.getLogger("CreativeEnginePro").debug("subprocess terminate/wait failed", exc_info=True)
            finally:
                self.current_subprocess = None

        # 删除可能未完成的输出文件
        if self.current_out_path and os.path.exists(self.current_out_path):
            try:
                os.remove(self.current_out_path)
                self.emit_log("已删除未完成的临时文件")
            except Exception as e:
                self.emit_log(f"删除失败: {e}")
    def emit_log(self, message):
        if self.log_signal:
            self.log_signal.emit(message)
        else:
            import logging; logging.getLogger("CreativeEnginePro").info(f"[VideoEngine] {message}")

    def analyze_tail_breakpoint(self, video_path, search_seconds=6):
        """
        升级版：SSIM + 亮度检测 (黑场判定) + 置信度
        """
        try:
            from skimage.metrics import structural_similarity as ssim
        except ImportError:
            return 0, 0 # 兜底

        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0 or fps <= 0: return 0, 0
        
        search_frames = int(fps * search_seconds)
        start_frame = max(0, total_frames - search_frames)
        
        last_frame_data = None
        # 默认返回总时长的80%作为位置，置信度为0
        final_point = (total_frames * 0.8) / fps
        confidence = 0

        # 逐帧倒序扫描
        frames_buffer = []

        # Step 1：顺序读取（只做一次解码）
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        for _ in range(start_frame, total_frames):
            ret, frame = cap.read()
            if not ret:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small_gray = cv2.resize(gray, (128, 128))
            frames_buffer.append(small_gray)

        cap.release()

        # Step 2：倒序分析
        last_frame_data = None

        for i in range(len(frames_buffer) - 1, 0, -1):
            small_gray = frames_buffer[i]

            brightness = np.mean(small_gray)
            is_white_flash = brightness > 230
            is_black_frame = brightness < 8.0

            if last_frame_data is not None:
                s_score = ssim(last_frame_data, small_gray)

                if is_black_frame and s_score < 0.5:
                    final_point = (start_frame + i + 1) / fps
                    confidence = 95
                    break
                elif is_white_flash:
                    final_point = (start_frame + i + 1) / fps
                    confidence = 79
                    break
                elif s_score < 0.45:
                    final_point = (start_frame + i + 1) / fps
                    confidence = 75
                    break

            last_frame_data = small_gray

        return final_point, confidence

    def _fallback_analyze(self, video_path, search_seconds):
        # 这是一个备用函数，万一用户没装库，用你原来的直方图逻辑，防止程序崩溃
        # (这里放你原本的那段代码内容即可)
        return 0

    def make_blur_fill(self, clip, target_w, target_h):
        import moviepy.video.fx.all as vfx
        duration = clip.duration

        # --- 核心修改：采样率从 /5 改为 /60 (极其细小的采样) ---
        # 1080 宽缩到 18 像素，这会彻底抹除形状，只剩颜色感
        low_res_w = max(8, int(target_w / 60)) 
        
        bg = clip.resize(width=low_res_w)
        if bg.h < (target_h / 60):
            bg = clip.resize(height=int(target_h / 60))

        # 拉伸回满屏 (此时已经是极致模糊的色块流了)
        bg = bg.resize(width=target_w)
        if bg.h < target_h:
            bg = bg.resize(height=target_h)

        bg = bg.crop(x_center=bg.w/2, y_center=bg.h/2, width=target_w, height=target_h)

        # 尝试滤镜（如果还是失败也没关系，上面的 1/60 缩放已经足够糊了）
                # 模糊处理（MoviePy 正确参数是 radius）
        try:
            bg = bg.fx(vfx.blur, radius=5)
        except Exception:
            import logging; logging.getLogger("CreativeEnginePro").debug("blur filter failed", exc_info=True)
            # 前面 1/60 缩放已经足够模糊，不影响

        # 亮度调低到 0.4 (更有氛围感)
        bg = bg.fx(vfx.colorx, 0.4).set_duration(duration)

        fg = clip.resize(height=target_h).set_position("center").set_duration(duration)
        return CompositeVideoClip([bg, fg], size=(target_w, target_h)).set_duration(duration)
    def make_center_crop(self, clip, target_w, target_h):
        """宽变窄逻辑：取中间，高度固定"""
        duration = clip.duration
        target_ratio = target_w / target_h
        
        # 以高度为基准计算需要的宽度
        required_w = clip.h * target_ratio
        
        # 执行中心裁剪
        cropped = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=required_w, height=clip.h)
        
        # 缩放到目标尺寸
        return cropped.resize(width=target_w, height=target_h).set_duration(duration)
    def process_task(self, task, config):
        """决策中心：严格区分全量渲染与极速导出，并支持立即中断"""
        # 1. 每次任务开始前，重置取消标志
        self.is_cancelled = False
        
        try:
            video_path = task['path']
            # 获取用户在下拉框选中的模式文本 (对应你右侧参数面板的下拉框)
            mode = str(config.get('ratio_mode', "")).strip()

            # --- 步骤 A: 检查是否已点击停止 ---
            if self.is_cancelled: 
                return False

            # --- 步骤 B: 确定时间断点 (智能分析或手动设定) ---
            if 'precise_cut_time' not in task or task['precise_cut_time'] is None:
                self.emit_log(f"启动智能分析: {task['name']}...")
                # 调用你类中已有的分析函数
                task['precise_cut_time'], _ = self.analyze_tail_breakpoint(video_path)
            else:
                self.emit_log(f"使用设定断点: {task['precise_cut_time']:.2f}s")

            # --- 步骤 C: 再次检查中断 ---
            if self.is_cancelled: 
                return False

            # --- 步骤 D: 判定分流逻辑 ---
            # 只要模式文本里包含以下关键词，说明需要改变画幅，必须走全量渲染
            render_keywords = ["9:16", "16:9", "1:1", "4:5", "填充", "裁剪", "适配"]
            need_full_render = any(k in mode for k in render_keywords)

            # --- 步骤 E: 执行对应的导出流程 ---
            if need_full_render:
                self.emit_log(f"模式 [{mode}] 要求修改尺寸 -> 进入全量渲染")
                # 调用全量渲染 (MoviePy 逻辑)
                return self.full_render_process(task, config)
            else:
                self.emit_log(f"模式为保持原样 -> 进入极速导出")
                # 实际进度由 stream_ffmpeg_progress 实时报告
                return self.fast_remux_process(task, config)

        except Exception as e:
            # 如果是因为被 taskkill 杀掉进程导致的报错，记录为“已终止”
            if self.is_cancelled:
                self.emit_log("渲染任务已由用户强制终止")
            else:
                self.emit_log(f"任务执行异常: {str(e)}")
            return False
    def full_render_process(self, task, config):
        """全量渲染逻辑：处理背景板、比例转换，支持取消"""
        clip = None
        final_video = None
        out_path = None

        # 定义可取消的进度监听器
        class CancelableLogger(MoviePyProgressListener):
            def __init__(self, log_signal, processor):
                super().__init__(log_signal)
                self.processor = processor

            def callback(self, **changes):
                if self.processor.is_cancelled:
                    raise Exception("UserCancelled")
                super().callback(**changes)

        try:
            # --- 1. 基础参数准备 ---
            video_path = task['path']
            tail_path = config.get('tail_path')
            mode = str(config.get('ratio_mode', "")).strip()
            
            clip = VideoFileClip(video_path)
            
            # --- 2. 强制解析目标尺寸 ---
            if "9:16" in mode: tw, th = 1080, 1920
            elif "16:9" in mode: tw, th = 1920, 1080
            elif "4:5" in mode: tw, th = 1080, 1350
            elif "1:1" in mode: tw, th = 1080, 1080
            else: tw, th = clip.w, clip.h

            duration = task.get('precise_cut_time', clip.duration)
            main_content = clip.subclip(0, duration)

            # 检查取消
            if self.is_cancelled:
                return False

            # --- 3. 图像变换逻辑 ---
            input_ratio = clip.w / clip.h
            target_ratio = tw / th
            
            if input_ratio < target_ratio - 0.01:
                self.emit_log("执行模糊填充逻辑...")
                processed_main = self.make_blur_fill(main_content, tw, th)
            elif input_ratio > target_ratio + 0.01:
                self.emit_log("执行中心裁剪逻辑...")
                processed_main = self.make_center_crop(main_content, tw, th)
            else:
                self.emit_log("比例一致，正在重绘尺寸...")
                processed_main = main_content.resize(width=tw, height=th)

            # 检查取消
            if self.is_cancelled:
                return False

            # --- 4. 合并尾页 ---
            if tail_path and os.path.exists(tail_path):
                from moviepy.editor import AudioClip
                
                tail_clip = VideoFileClip(tail_path, has_mask=True).resize(width=tw, height=th)
                if tail_clip.mask is not None:
                    tail_clip = tail_clip.on_color(size=(tw, th), color=(0,0,0), col_opacity=1)
                    tail_clip = tail_clip.set_mask(None)

                if processed_main.audio is not None and tail_clip.audio is None:
                    silent_audio = AudioClip(lambda t: [0, 0], duration=tail_clip.duration).set_fps(44100)
                    tail_clip = tail_clip.set_audio(silent_audio)
                elif processed_main.audio is None and tail_clip.audio is not None:
                    tail_clip = tail_clip.without_audio()

                tail_clip = tail_clip.set_fps(processed_main.fps if processed_main.fps else 30)
                final_video = concatenate_videoclips([processed_main, tail_clip], method="compose")
            else:
                final_video = processed_main

            # 检查取消
            if self.is_cancelled:
                return False

            # --- 5. 导出 ---
            final_name = config.get('final_save_name', os.path.splitext(task['name'])[0])
            base_out_path = os.path.join(config['out_dir'], final_name + ".mp4")
            out_path = base_out_path
            counter = 1
            while os.path.exists(out_path):
                out_path = os.path.join(config['out_dir'], f"{final_name}_{counter}.mp4")
                counter += 1
            
            # 记录输出路径,用于取消时删除
            self.current_out_path = out_path
            
            actual_file_name = os.path.basename(out_path)
            self.emit_log(f"正在全量渲染: {actual_file_name}")
            self.emit_log(f"SET_CURRENT_PATH:{out_path}")
            
            # 使用可取消的日志监听器
            logger = CancelableLogger(self.log_signal, self)
            
            final_video.write_videofile(
                out_path, 
                codec="libx264", 
                audio_codec="aac", 
                fps=clip.fps or 25, 
                threads=8,        
                preset="ultrafast", 
                logger=logger, 
                ffmpeg_params=["-pix_fmt", "yuv420p", "-y"]
            )
            return True

        except Exception as e:
            if "UserCancelled" in str(e):
                self.emit_log("导出已取消")
                # 删除不完整的输出文件
                if out_path and os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except Exception as del_err:
                        self.emit_log(f"删除不完整文件失败: {del_err}")
                return False
            else:
                self.emit_log(f"渲染失败: {str(e)}")
                import logging; logging.getLogger("CreativeEnginePro").error("Full render exception", exc_info=True)
                return False
        finally:
            # 释放资源
            if 'clip' in locals() and clip:
                try: clip.close()
                except Exception: import logging; logging.getLogger("CreativeEnginePro").debug("clip.close() failed", exc_info=True)
            if 'tail_clip' in locals() and tail_clip:
                try: tail_clip.close()
                except Exception: import logging; logging.getLogger("CreativeEnginePro").debug("tail_clip.close() failed", exc_info=True)
            if 'final_video' in locals() and final_video:
                try: final_video.close()
                except Exception: import logging; logging.getLogger("CreativeEnginePro").debug("final_video.close() failed", exc_info=True)
            # 清除当前输出路径引用
            self.current_out_path = None
            import gc
            gc.collect()