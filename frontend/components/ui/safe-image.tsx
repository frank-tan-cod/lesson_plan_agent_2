"use client";

import Image, { type ImageLoader, type ImageProps } from "next/image";

const passthroughLoader: ImageLoader = ({ src }) => src;

type SafeImageProps = Omit<ImageProps, "loader">;

export function SafeImage(props: SafeImageProps) {
  const { alt, ...rest } = props;
  return <Image {...rest} alt={alt} loader={passthroughLoader} unoptimized />;
}
