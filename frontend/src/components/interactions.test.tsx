import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError, type Resume } from '@/lib/api';
import { AuthScreen } from './auth-screen';
import { UploadDialog } from './upload-dialog';
const mocks = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn(), signIn: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => mocks }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ status: 'anonymous', signIn: mocks.signIn }) }));
beforeEach(() => vi.clearAllMocks());

it('invalid registration explains fields, focuses first error, and never submits', async () => {
  const register = vi.spyOn(api, 'register'); const user = userEvent.setup(); render(<AuthScreen mode="register" />);
  await user.click(screen.getByRole('button', { name: '创建账户' }));
  expect(screen.getByLabelText('邮箱地址')).toHaveFocus(); expect(screen.getByText('密码需要 12–128 个字符。')).toBeVisible(); expect(register).not.toHaveBeenCalled();
});
it('password visibility toggle preserves the entered value', async () => {
  const user = userEvent.setup(); render(<AuthScreen mode="login" />);
  await user.type(screen.getByLabelText('密码', { exact: true }), 'Synthetic-secret');
  await user.click(screen.getByRole('button', { name: '显示密码' }));
  expect(screen.getByLabelText('密码', { exact: true })).toHaveAttribute('type', 'text');
  await user.click(screen.getByRole('button', { name: '隐藏密码' }));
  expect(screen.getByLabelText('密码', { exact: true })).toHaveValue('Synthetic-secret');
});
it('successful registration goes to login and does not silently create a session', async () => {
  vi.spyOn(api, 'register').mockResolvedValue({ id: 'test', email: 'synthetic@example.com', created_at: '2026-10-11' });
  const user = userEvent.setup(); render(<AuthScreen mode="register" />);
  await user.type(screen.getByLabelText('邮箱地址'), 'synthetic@example.com');
  await user.type(screen.getByLabelText('密码', { exact: true }), 'Synthetic-secret');
  await user.type(screen.getByLabelText('邀请码'), 'a'.repeat(43));
  await user.click(screen.getByRole('button', { name: '创建账户' }));
  await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith('/login?registered=1')); expect(mocks.signIn).not.toHaveBeenCalled();
});
it('non-PDF selection cannot submit an upload', async () => {
  const upload = vi.spyOn(api, 'upload'); const user = userEvent.setup({ applyAccept: false }); render(<UploadDialog open onOpenChange={vi.fn()} />);
  await user.upload(screen.getByLabelText('选择 PDF 简历'), new File(['x'], 'resume.txt', { type: 'text/plain' }));
  expect(screen.getByRole('alert')).toHaveTextContent('.pdf'); expect(screen.getByRole('button', { name: '上传简历' })).toBeDisabled(); expect(upload).not.toHaveBeenCalled();
});
it('100 percent transferred waits for backend confirmation and prevents duplicate submission', async () => {
  let complete!: (resume: Resume) => void;
  const upload = vi.spyOn(api, 'upload').mockImplementation((_file, progress) => { progress?.(100); return new Promise(resolve => { complete = resolve; }); });
  const close = vi.fn(); const user = userEvent.setup(); render(<UploadDialog open onOpenChange={close} />);
  await user.upload(screen.getByLabelText('选择 PDF 简历'), new File(['synthetic'], 'resume.pdf', { type: 'application/pdf' }));
  await user.click(screen.getByRole('button', { name: '上传简历' }));
  expect(screen.getByText('文件已发送，正在保存并读取文本…')).toBeVisible(); expect(close).not.toHaveBeenCalled(); expect(upload).toHaveBeenCalledTimes(1);
  complete({ id: 'file', file_name: 'resume.pdf', file_type: 'application/pdf', created_at: '2026-10-11' });
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/resumes/file'));
});
it('unknown upload result requires checking saved files before resubmission', async () => {
  vi.spyOn(api, 'upload').mockRejectedValue(new ApiError(0, 'upload_result_unknown', '上传结果尚未确认'));
  const user = userEvent.setup(); render(<UploadDialog open onOpenChange={vi.fn()} />);
  await user.upload(screen.getByLabelText('选择 PDF 简历'), new File(['synthetic'], 'resume.pdf', { type: 'application/pdf' }));
  await user.click(screen.getByRole('button', { name: '上传简历' }));
  expect(await screen.findByRole('link', { name: /先查看简历列表/ })).toHaveAttribute('href', '/resumes');
  expect(screen.getByRole('button', { name: '上传简历' })).toBeDisabled();
});
