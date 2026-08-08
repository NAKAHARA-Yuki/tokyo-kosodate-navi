"use client"; // エラーバウンダリは Client Component である必要がある

import { useEffect } from "react";
import { Button } from "@/components/dads/button";
import { Heading, HeadingTitle } from "@/components/dads/heading";
import { Link } from "@/components/dads/link";

/**
 * 想定外の例外が起きたときの画面（`app/` 配下すべてを覆うエラーバウンダリ）。
 *
 * **backend のステータスコードなど内部の事情を画面に出さないこと。**
 * 利用者にとって意味が無いうえ、backend の状態を外に晒すことになる。
 * Server Component が投げた例外は Next.js が本番ビルドで匿名化するため、
 * ここで参照できるのは `digest`（サーバ側ログと突き合わせるためのハッシュ）だけになる。
 */
export default function ErrorPage({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  // Next.js 16.2 で `reset` に代わって追加された。再取得したうえで再レンダーする
  // （`reset` は再取得をしないため、backend 起因の失敗では復帰できない）。
  unstable_retry: () => void;
}) {
  useEffect(() => {
    // 画面には出さず、ブラウザのコンソールにだけ残す。
    // サーバ側で起きた例外は Next.js が digest 付きでサーバのログに出している。
    console.error(error);
  }, [error]);

  return (
    <main className="mx-auto max-w-3xl p-6">
      <Heading size="28" rule="4" className="mb-6">
        <HeadingTitle level="h1">ページを表示できませんでした</HeadingTitle>
      </Heading>

      <p className="text-std-16N-170">
        一時的に情報を取得できない状態になっています。
        少し時間をおいてから、もう一度お試しください。
      </p>
      <p className="mt-2 text-std-16N-170">
        お急ぎの場合は、お住まいの自治体の窓口へ直接お問い合わせください。
      </p>

      <div className="mt-6 flex flex-wrap items-center gap-4">
        <Button variant="solid-fill" size="md" onClick={() => unstable_retry()}>
          もう一度読み込む
        </Button>
        <Link href="/">制度の一覧に戻る</Link>
      </div>

      {error.digest && (
        // 問い合わせを受けたときにサーバ側のログと突き合わせるための番号。
        // それ自体は内部情報を含まない。
        <p className="mt-8 text-std-16N-170 text-solid-gray-700">
          エラー番号: {error.digest}
        </p>
      )}
    </main>
  );
}
